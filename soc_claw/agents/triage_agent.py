import json
import time

from soc_claw.utils import (
    log_tool_call,
    call_llm,
)
from soc_claw.schemas import TriageVerdict
from soc_claw.tools.ip_reputation import ip_reputation
from soc_claw.tools.mitre_lookup import mitre_lookup
from soc_claw.tools.asset_lookup import asset_lookup

SYSTEM_PROMPT = """You are a SOC Tier 2 security analyst performing alert triage. You are given a raw security alert along with pre-gathered enrichment data from three sources: IP reputation, asset inventory, and MITRE ATT&CK technique mapping.

Analyze the alert and enrichment data, then follow this workflow:

STEP 1 — CORRELATE: Analyze the enrichment results together:
- Does the IP reputation indicate known malicious infrastructure?
- Is the asset critical to business operations?
- Do the MITRE techniques suggest a known attack pattern or campaign?
- What is the overall threat narrative?

STEP 2 — SCORE: Assign severity using this rubric:
- P1 CRITICAL: Confirmed malicious activity on critical/high asset, active data exfiltration, or indicators matching a known APT campaign. Requires immediate containment.
- P2 HIGH: Likely malicious on medium asset, or confirmed malicious on low asset with lateral movement potential. Requires urgent investigation.
- P3 MEDIUM: Suspicious activity needing investigation, no confirmed IOCs. Anomalous but potentially legitimate behavior.
- P4 LOW: Informational, known false positive pattern, or benign activity triggering a broad detection rule.

STEP 3 — OUTPUT: Return a JSON object with exactly these fields:
{
  "severity": "P1|P2|P3|P4",
  "confidence": <int 0-100>,
  "reasoning": "<2-3 sentence explanation of your triage decision>",
  "mitre_techniques": ["T1059.001", ...],
  "iocs_found": [{"indicator": "...", "type": "ip|domain|hash", "threat_score": <int>}],
  "asset_criticality": "critical|high|medium|low",
  "recommended_urgency": "immediate|urgent|standard|monitor"
}

Be precise. Be consistent. When in doubt between two severity levels, choose the higher one — missed true positives are more costly than false escalations.

IMPORTANT: Output ONLY the JSON object. No other text."""

_RETRY_HINT = (
    "Please output ONLY a valid JSON object with fields: severity, confidence, "
    "reasoning, mitre_techniques, iocs_found, asset_criticality, recommended_urgency."
)


async def _run_enrichment(alert: dict) -> tuple[dict, dict, list, list, dict | None]:
    """Run all enrichment tools concurrently on an alert.

    Returns (ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result).
    Tools run via ``asyncio.to_thread`` so they don't block the event
    loop and latencies don't stack when tools become real API calls.
    """
    import asyncio

    from soc_claw.telemetry import get_tracer
    tracer = get_tracer()

    tool_calls_log = []

    dest_ip = alert.get("dest_ip", "")
    source_ip = alert.get("source_ip", "")
    hostname = alert.get("hostname", "")
    behavior = f"{alert.get('rule_name', '')} {alert.get('payload', '')}"

    # Run the three core lookups concurrently
    async def _timed(name, func, *args):
        start = time.perf_counter()
        result = await asyncio.to_thread(func, *args)
        elapsed = int((time.perf_counter() - start) * 1000)
        return name, args, result, elapsed

    with tracer.start_as_current_span(
        "enrichment.run",
        attributes={"alert.id": alert.get("id", "")},
    ) as span:
        tasks = [
            _timed("ip_reputation", ip_reputation, dest_ip),
            _timed("asset_lookup", asset_lookup, hostname),
            _timed("mitre_lookup", mitre_lookup, behavior),
        ]

        # Optionally check source IP if it's external
        check_source = (
            source_ip
            and not source_ip.startswith("10.")
            and not source_ip.startswith("192.168.")
        )
        if check_source:
            tasks.append(_timed("ip_reputation", ip_reputation, source_ip))

        span.set_attribute("tools_run", len(tasks))
        results = await asyncio.gather(*tasks)

    # Unpack results in known order
    ip_name, ip_args, ip_result, ip_ms = results[0]
    asset_name, asset_args, asset_result, asset_ms = results[1]
    mitre_name, mitre_args, mitre_results, mitre_ms = results[2]

    log_tool_call("ip_reputation", {"ip": dest_ip}, ip_result, ip_ms)
    tool_calls_log.append({"tool": "ip_reputation", "input": {"ip": dest_ip}, "output": ip_result})

    log_tool_call("asset_lookup", {"hostname": hostname}, asset_result, asset_ms)
    tool_calls_log.append({"tool": "asset_lookup", "input": {"hostname": hostname}, "output": asset_result})

    log_tool_call("mitre_lookup", {"behavior": behavior[:200]}, mitre_results, mitre_ms)
    tool_calls_log.append({"tool": "mitre_lookup", "input": {"behavior": behavior[:200]}, "output": mitre_results})

    source_ip_result = None
    if check_source:
        _, _, source_ip_result, src_ms = results[3]
        log_tool_call("ip_reputation", {"ip": source_ip}, source_ip_result, src_ms)
        tool_calls_log.append({"tool": "ip_reputation", "input": {"ip": source_ip}, "output": source_ip_result})

    return ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result


async def run_triage(alert: dict, steering_context: str = None) -> dict:
    """Run the Triage Agent on a raw alert.

    Calls tools directly for enrichment, then sends enriched context to the LLM
    for analysis and severity scoring.
    """
    # Step 1: Run enrichment tools directly
    ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result = await _run_enrichment(alert)

    # Step 2: Build enriched prompt for the LLM
    alert_json = json.dumps(alert, indent=2)
    enrichment = {
        "dest_ip_reputation": ip_result,
        "asset_info": asset_result,
        "mitre_techniques": mitre_results,
    }
    if source_ip_result:
        enrichment["source_ip_reputation"] = source_ip_result

    enrichment_json = json.dumps(enrichment, indent=2)

    if steering_context:
        user_content = (
            f"ANALYST CONTEXT: {steering_context}\n\n"
            f"ALERT:\n{alert_json}\n\n"
            f"ENRICHMENT DATA:\n{enrichment_json}"
        )
    else:
        user_content = (
            f"ALERT:\n{alert_json}\n\n"
            f"ENRICHMENT DATA:\n{enrichment_json}"
        )

    # Step 3: LLM call via shared scaffold
    def _default():
        severity = "P3"
        if ip_result.get("verdict") == "malicious" and asset_result.get("criticality") in ("critical", "high"):
            severity = "P1"
        elif ip_result.get("verdict") == "malicious":
            severity = "P2"
        return {
            "severity": severity,
            "confidence": 30,
            "reasoning": "Failed to parse LLM response. Severity estimated from enrichment data.",
            "mitre_techniques": [m.get("technique_id", "") for m in mitre_results],
            "iocs_found": [{"indicator": alert.get("dest_ip", ""), "type": "ip", "threat_score": ip_result.get("threat_score", 0)}] if ip_result.get("threat_score", 0) > 0 else [],
            "asset_criticality": asset_result.get("criticality", "medium"),
            "recommended_urgency": "immediate" if severity == "P1" else "urgent" if severity == "P2" else "standard",
        }

    verdict, inference_ms, route, content = await call_llm(
        agent_name="triage",
        system_prompt=SYSTEM_PROMPT,
        user_content=user_content,
        schema_class=TriageVerdict,
        retry_hint=_RETRY_HINT,
        default_factory=_default,
    )

    # Attach enrichment metadata (triage-specific)
    verdict["_tool_calls"] = tool_calls_log

    return verdict
