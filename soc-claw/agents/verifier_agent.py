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
    log_inference,
    log_verification,
    MODEL_NAME,
)

SYSTEM_PROMPT = """You are a senior SOC analyst performing quality assurance on alert triage decisions. You receive a raw security alert AND the Triage Agent's verdict (severity, confidence, reasoning, enrichment data).

You do NOT have access to any tools. You do NOT re-run enrichment. You evaluate whether the Triage Agent's reasoning is sound and the severity matches the evidence it gathered.

VERIFICATION CHECKLIST — evaluate each:

1. EVIDENCE-SEVERITY ALIGNMENT:
   Does the assigned severity match the evidence?
   - P1 requires: confirmed malicious IOC + critical/high asset, OR active exfiltration, OR known APT match
   - P2 requires: likely malicious + medium asset, OR confirmed malicious + low asset with lateral movement
   - P3 requires: suspicious but unconfirmed, no confirmed IOCs
   - P4 requires: clear indicators of benign/known-FP activity
   Flag any mismatch between evidence strength and severity level.

2. REASONING COMPLETENESS:
   Did the Triage Agent consider ALL three enrichment sources?
   - IP reputation result: was it factored into the decision?
   - Asset criticality: was it weighted appropriately? (a P3 on a critical asset with any IOC is suspicious)
   - MITRE mapping: were the matched techniques considered in the threat narrative?
   Flag if any enrichment source was gathered but ignored in the reasoning.

3. LOGICAL CONSISTENCY:
   Does the reasoning chain logically lead to the stated severity?
   - High confidence (>80%) with weak evidence = inconsistent
   - P4 verdict on a critical asset with known C2 connection = red flag
   - P1 verdict with no confirmed IOCs and unknown IPs = over-escalation
   Flag logical gaps or contradictions.

4. BIAS CHECK:
   Common triage errors to watch for:
   - Anchoring: over-weighting the first piece of evidence (usually IP reputation)
   - Asset blindness: ignoring criticality tier when scoring
   - FP fatigue: under-scoring alerts that resemble common false positives but have subtle differences
   - Escalation bias: scoring everything as P1/P2 out of caution without evidence

DECISION — choose exactly one:

- CONFIRMED: The triage is sound. Severity and reasoning are consistent with the evidence. Pass through unchanged.
- ADJUSTED: The triage has a specific error. State the corrected severity and explain exactly what was wrong.
- FLAGGED: The evidence is genuinely ambiguous and you cannot confidently confirm or adjust. Pause for human analyst review.

OUTPUT exactly this JSON:
{
  "decision": "confirmed|adjusted|flagged",
  "original_severity": "P1|P2|P3|P4",
  "verified_severity": "P1|P2|P3|P4",
  "confidence_in_verification": <int 0-100>,
  "reasoning": "<2-3 sentences explaining your verification decision>",
  "issues_found": ["<specific issue 1>", "<specific issue 2>"],
  "checks_passed": ["evidence_alignment", "reasoning_completeness", "logical_consistency", "bias_check"],
  "checks_failed": [],
  "recommendation": "<what should happen next>"
}

IMPORTANT GUIDELINES:
- Be rigorous but fair. The goal is catching errors, not second-guessing every reasonable decision.
- If the triage is reasonable, confirm it quickly. Don't manufacture issues.
- When you adjust, always adjust by exactly one severity level unless there's an egregious error.
- "Flagged" should be rare — only when you genuinely cannot determine the correct severity.
- Your verification adds latency to the pipeline. Be decisive, not deliberative."""


async def run_verification(alert: dict, triage_result: dict, steering_context: str = None) -> dict:
    """Run the Verifier Agent on a triage result.

    Returns the verification decision dict.
    """
    alert_json = json.dumps(alert, indent=2)

    # Remove internal metadata from triage_result for the verifier
    triage_for_verifier = {k: v for k, v in triage_result.items() if not k.startswith("_")}
    triage_json = json.dumps(triage_for_verifier, indent=2)

    if steering_context:
        user_content = (
            f"ANALYST CONTEXT: {steering_context}\n\n"
            f"ALERT:\n{alert_json}\n\n"
            f"TRIAGE VERDICT:\n{triage_json}"
        )
    else:
        user_content = f"ALERT:\n{alert_json}\n\nTRIAGE VERDICT:\n{triage_json}"

    # Route the request
    route, reason = route_request(user_content)
    log_routing_decision("verifier", route, reason, user_content)
    client = get_client(route)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    inference_start = time.perf_counter()

    # Single call — NO tools
    response = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
    )

    inference_ms = int((time.perf_counter() - inference_start) * 1000)
    log_inference("verifier", route, inference_ms)

    content = response.choices[0].message.content or ""

    try:
        result = extract_json(content)
    except ValueError:
        # Retry once
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "Please output valid JSON matching the required schema with fields: decision, original_severity, verified_severity, confidence_in_verification, reasoning, issues_found, checks_passed, checks_failed, recommendation.",
        })
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        try:
            result = extract_json(content)
        except ValueError:
            # Fail-open: treat as confirmed
            result = {
                "decision": "confirmed",
                "original_severity": triage_result.get("severity", "P3"),
                "verified_severity": triage_result.get("severity", "P3"),
                "confidence_in_verification": 50,
                "reasoning": "Verification failed to produce valid JSON. Defaulting to confirmed (fail-open).",
                "issues_found": ["verifier_json_parse_failure"],
                "checks_passed": [],
                "checks_failed": ["json_output"],
                "recommendation": "Manual review recommended due to verification failure.",
            }

    # Log the verification decision
    log_verification(
        alert.get("id", "unknown"),
        result.get("original_severity", ""),
        result.get("verified_severity", ""),
        result.get("decision", ""),
        result.get("issues_found", []),
    )

    result["_inference_ms"] = inference_ms
    result["_route"] = route
    result["_raw_response"] = content

    return result
