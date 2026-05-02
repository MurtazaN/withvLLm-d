import json
import time

from soc_claw.audit import log_tool_call
from soc_claw.llm import call_llm
from soc_claw.schemas import TriageVerdict
from soc_claw.tools import registry

SYSTEM_PROMPT = """You are a SOC Tier 2 security analyst performing alert triage. You are given a raw security alert along with pre-gathered enrichment data.

Available enrichment sources:
{tool_descriptions}

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


async def _run_enrichment(alert: dict) -> tuple[dict, list]:
    """Run all registered enrichment tools concurrently on an alert.

    Returns (enrichment_dict, tool_calls_log).
    """
    import asyncio

    from soc_claw.telemetry import get_tracer
    tracer = get_tracer()

    tool_calls_log = []
    enrichment = {}
    tools = registry.get_all()

    async def _timed(tool):
        start = time.perf_counter()
        result = await asyncio.to_thread(tool.run, alert)
        elapsed = int((time.perf_counter() - start) * 1000)
        return tool.name, result, elapsed

    with tracer.start_as_current_span(
        "enrichment.run",
        attributes={"alert.id": alert.get("id", "")},
    ) as span:
        span.set_attribute("tools_run", len(tools))
        
        if not tools:
            return enrichment, tool_calls_log

        tasks = [_timed(t) for t in tools]
        results = await asyncio.gather(*tasks)

    for name, result, ms in results:
        enrichment[name] = result
        log_tool_call(name, {"alert_id": alert.get("id", "")}, result, ms)
        tool_calls_log.append({"tool": name, "output": result})

    return enrichment, tool_calls_log


async def run_triage(alert: dict, steering_context: str = None) -> dict:
    """Run the Triage Agent on a raw alert.

    Calls tools directly for enrichment, then sends enriched context to the LLM
    for analysis and severity scoring.
    """
    # Step 1: Run enrichment tools directly
    enrichment, tool_calls_log = await _run_enrichment(alert)

    # Step 2: Build enriched prompt for the LLM
    alert_json = json.dumps(alert, indent=2)
    enrichment_json = json.dumps(enrichment, indent=2)

    tool_descriptions = "\n".join(f"- {t.name}: {t.description}" for t in registry.get_all())
    system_prompt = SYSTEM_PROMPT.replace("{tool_descriptions}", tool_descriptions)

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
        ip_res = enrichment.get("ip_reputation", {}).get("dest_ip", {})
        asset_res = enrichment.get("asset_lookup", {})
        mitre_res = enrichment.get("mitre_lookup", [])

        if ip_res.get("verdict") == "malicious" and asset_res.get("criticality") in ("critical", "high"):
            severity = "P1"
        elif ip_res.get("verdict") == "malicious":
            severity = "P2"
        return {
            "severity": severity,
            "confidence": 30,
            "reasoning": "Failed to parse LLM response. Severity estimated from enrichment data.",
            "mitre_techniques": [m.get("technique_id", "") for m in mitre_res],
            "iocs_found": [{"indicator": alert.get("dest_ip", ""), "type": "ip", "threat_score": ip_res.get("threat_score", 0)}] if ip_res.get("threat_score", 0) > 0 else [],
            "asset_criticality": asset_res.get("criticality", "medium"),
            "recommended_urgency": "immediate" if severity == "P1" else "urgent" if severity == "P2" else "standard",
        }

    verdict, inference_ms, route, content = await call_llm(
        agent_name="triage",
        system_prompt=system_prompt,
        user_content=user_content,
        schema_class=TriageVerdict,
        retry_hint=_RETRY_HINT,
        default_factory=_default,
    )

    # Attach enrichment metadata (triage-specific)
    verdict.setdefault("_meta", {})["tool_calls"] = tool_calls_log

    return verdict
