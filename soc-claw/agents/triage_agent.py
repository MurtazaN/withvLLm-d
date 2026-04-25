import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import (
    extract_json,
    get_client,
    route_request,
    log_routing_decision,
    log_tool_call,
    log_inference,
    MODEL_NAME,
)
from tools.ip_reputation import ip_reputation
from tools.mitre_lookup import mitre_lookup
from tools.asset_lookup import asset_lookup

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


def _run_enrichment(alert: dict) -> tuple[dict, dict, list, list]:
    """Run all three enrichment tools on an alert. Returns (ip_result, asset_result, mitre_results, tool_calls_log)."""
    tool_calls_log = []

    # IP Reputation - check dest_ip (and source_ip if external)
    dest_ip = alert.get("dest_ip", "")
    source_ip = alert.get("source_ip", "")

    start = time.perf_counter()
    ip_result = ip_reputation(dest_ip)
    elapsed = int((time.perf_counter() - start) * 1000)
    log_tool_call("ip_reputation", {"ip": dest_ip}, ip_result, elapsed)
    tool_calls_log.append({"tool": "ip_reputation", "input": {"ip": dest_ip}, "output": ip_result})

    # Also check source IP if it's external
    source_ip_result = None
    if source_ip and not source_ip.startswith("10.") and not source_ip.startswith("192.168."):
        start = time.perf_counter()
        source_ip_result = ip_reputation(source_ip)
        elapsed = int((time.perf_counter() - start) * 1000)
        log_tool_call("ip_reputation", {"ip": source_ip}, source_ip_result, elapsed)
        tool_calls_log.append({"tool": "ip_reputation", "input": {"ip": source_ip}, "output": source_ip_result})

    # Asset Lookup
    hostname = alert.get("hostname", "")
    start = time.perf_counter()
    asset_result = asset_lookup(hostname)
    elapsed = int((time.perf_counter() - start) * 1000)
    log_tool_call("asset_lookup", {"hostname": hostname}, asset_result, elapsed)
    tool_calls_log.append({"tool": "asset_lookup", "input": {"hostname": hostname}, "output": asset_result})

    # MITRE Lookup - build behavior description from alert
    behavior = f"{alert.get('rule_name', '')} {alert.get('payload', '')}"
    start = time.perf_counter()
    mitre_results = mitre_lookup(behavior)
    elapsed = int((time.perf_counter() - start) * 1000)
    log_tool_call("mitre_lookup", {"behavior": behavior[:200]}, mitre_results, elapsed)
    tool_calls_log.append({"tool": "mitre_lookup", "input": {"behavior": behavior[:200]}, "output": mitre_results})

    return ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result


async def run_triage(alert: dict, steering_context: str = None) -> dict:
    """Run the Triage Agent on a raw alert.

    Calls tools directly for enrichment, then sends enriched context to the LLM
    for analysis and severity scoring.
    """
    # Step 1: Run enrichment tools directly
    ip_result, asset_result, mitre_results, tool_calls_log, source_ip_result = _run_enrichment(alert)

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

    # Route the request
    route, reason = route_request(user_content)
    log_routing_decision("triage", route, reason, user_content)
    client = get_client(route)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    inference_start = time.perf_counter()

    # Single LLM call — no tool-calling API needed
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
    )

    inference_ms = int((time.perf_counter() - inference_start) * 1000)
    log_inference("triage", route, inference_ms)

    content = response.choices[0].message.content or ""

    try:
        verdict = extract_json(content)
    except ValueError:
        # Retry once with a reminder
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "Please output ONLY a valid JSON object with fields: severity, confidence, reasoning, mitre_techniques, iocs_found, asset_criticality, recommended_urgency.",
        })
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        try:
            verdict = extract_json(content)
        except ValueError:
            # Default verdict based on enrichment data
            severity = "P3"
            if ip_result.get("verdict") == "malicious" and asset_result.get("criticality") in ("critical", "high"):
                severity = "P1"
            elif ip_result.get("verdict") == "malicious":
                severity = "P2"

            verdict = {
                "severity": severity,
                "confidence": 30,
                "reasoning": "Failed to parse LLM response. Severity estimated from enrichment data.",
                "mitre_techniques": [m.get("technique_id", "") for m in mitre_results],
                "iocs_found": [{"indicator": alert.get("dest_ip", ""), "type": "ip", "threat_score": ip_result.get("threat_score", 0)}] if ip_result.get("threat_score", 0) > 0 else [],
                "asset_criticality": asset_result.get("criticality", "medium"),
                "recommended_urgency": "immediate" if severity == "P1" else "urgent" if severity == "P2" else "standard",
            }

    # Attach enrichment metadata
    verdict["_tool_calls"] = tool_calls_log
    verdict["_inference_ms"] = inference_ms
    verdict["_route"] = route
    verdict["_raw_response"] = content

    return verdict
