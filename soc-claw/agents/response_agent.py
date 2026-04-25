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
    log_response_plan,
    MODEL_NAME,
)

SYSTEM_PROMPT = """You are a SOC incident responder. You receive a triaged and VERIFIED security alert with a final severity score, enrichment context, and verification status. Your job is to produce a prioritized response plan with specific next steps for the analyst to review and approve.

You do NOT execute actions directly. You RECOMMEND actions. The analyst will review each step and approve or reject it before execution. This is critical — automated containment without human approval is dangerous in production environments.

IMPORTANT: Use the "verified_severity" field, NOT the "original_severity". The Verifier Agent may have adjusted the severity. Always plan based on the verified/final verdict.

RESPONSE PLANNING BY SEVERITY:

P1 CRITICAL — Immediate containment required:
1. Isolate the affected host from the network via EDR
   - Why: prevent lateral movement and further data exfiltration
   - Urgency: execute within 5 minutes
2. Block all identified IOCs (IPs, domains, hashes) at the firewall
   - Why: cut off C2 communication and prevent reinfection
   - Urgency: execute within 10 minutes
3. Trigger forensic evidence collection (memory dump, disk image)
   - Why: preserve volatile evidence before it's lost
   - Urgency: execute within 15 minutes
4. Escalate to Tier 3 / Incident Response team
   - Why: P1 requires senior investigation and potential executive notification
   - Urgency: notify within 15 minutes
5. Create critical incident ticket with full context
   - Why: audit trail and handoff documentation

P2 HIGH — Urgent investigation required:
1. Block all identified IOCs at the firewall
   - Why: reduce exposure while investigation proceeds
   - Urgency: execute within 30 minutes
2. Create high-priority investigation ticket
   - Why: assign to Tier 2 for deep-dive analysis
3. Notify asset owner and request usage context
   - Why: may confirm or rule out legitimate activity
4. Escalate to Tier 2 with investigation brief
   - Why: needs skilled analysis beyond triage

P3 MEDIUM — Investigation needed, no immediate containment:
1. Create medium-priority investigation ticket
   - Why: queue for analyst review during normal operations
2. Add identified IOCs to watchlist (do NOT block yet)
   - Why: monitor for recurrence without disrupting operations
3. Request additional context from asset owner
   - Why: ambiguous alerts often resolve with business context
4. Schedule follow-up review in 24 hours
   - Why: ensure the alert doesn't go stale

P4 LOW — Log and monitor:
1. Create low-priority ticket for record-keeping
   - Why: audit trail, pattern detection over time
2. No containment or escalation actions needed
3. If this alert pattern recurs frequently: recommend tuning the detection rule
   - Why: reduce future false positive volume

OUTPUT exactly this JSON:
{
  "alert_id": "<from input>",
  "severity_acted_on": "<the verified severity you used>",
  "was_adjusted": <true if verifier changed the severity, false otherwise>,
  "response_plan": [
    {
      "step": 1,
      "action": "<specific action to take>",
      "action_type": "isolate_host|block_ioc|create_ticket|escalate|collect_forensics|add_to_watchlist|notify_owner|tune_rule",
      "target": "<hostname, IP, or system affected>",
      "reasoning": "<1 sentence: why this action is necessary>",
      "urgency": "immediate|within_30min|within_24hrs|when_convenient",
      "requires_approval": true|false
    }
  ],
  "incident_summary": "<2-3 sentence summary suitable for handoff to next shift or management briefing>",
  "analyst_notes": "<caveats, uncertainties, or recommended follow-up investigations>",
  "estimated_mttr_impact": "<how these actions reduce mean time to respond compared to manual triage>"
}

GUIDELINES:
- Every action must have a clear "why" — the analyst needs to understand the reasoning to make an approval decision.
- requires_approval should be TRUE for any containment action (isolate, block) and any escalation. It should be FALSE for logging-only actions (create low-priority ticket).
- Be specific in targets: "Block IP 185.220.101.42 at perimeter firewall" not "Block the bad IP."
- Include the action_type field so the UI can map each step to the correct execution function.
- If the Verifier flagged issues, reference them in analyst_notes."""


async def run_response(alert: dict, final_verdict: dict, steering_context: str = None) -> dict:
    """Run the Response Agent on a verified alert verdict.

    Returns the response plan dict.
    """
    alert_json = json.dumps(alert, indent=2)

    # Remove internal metadata from verdict
    verdict_for_agent = {k: v for k, v in final_verdict.items() if not k.startswith("_")}
    verdict_json = json.dumps(verdict_for_agent, indent=2)

    if steering_context:
        user_content = (
            f"ANALYST CONTEXT: {steering_context}\n\n"
            f"ALERT:\n{alert_json}\n\n"
            f"FINAL VERDICT:\n{verdict_json}"
        )
    else:
        user_content = f"ALERT:\n{alert_json}\n\nFINAL VERDICT:\n{verdict_json}"

    # Route the request
    route, reason = route_request(user_content)
    log_routing_decision("response", route, reason, user_content)
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
    log_inference("response", route, inference_ms)

    content = response.choices[0].message.content or ""

    try:
        result = extract_json(content)
    except ValueError:
        # Retry once
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": "Please output valid JSON matching the required schema with fields: alert_id, severity_acted_on, was_adjusted, response_plan, incident_summary, analyst_notes, estimated_mttr_impact.",
        })
        response = await client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
        )
        content = response.choices[0].message.content or ""
        try:
            result = extract_json(content)
        except ValueError:
            # Generate minimal default plan
            severity = final_verdict.get("verified_severity", final_verdict.get("severity", "P3"))
            result = _default_plan(alert, severity, final_verdict)

    # Log the response plan
    plan = result.get("response_plan", [])
    action_types = [s.get("action_type", "") for s in plan]
    approval_count = sum(1 for s in plan if s.get("requires_approval", False))
    log_response_plan(alert.get("id", "unknown"), len(plan), action_types, approval_count)

    result["_inference_ms"] = inference_ms
    result["_route"] = route
    result["_raw_response"] = content

    return result


def _default_plan(alert: dict, severity: str, verdict: dict) -> dict:
    """Generate a minimal default response plan when LLM output fails."""
    alert_id = alert.get("id", "unknown")
    hostname = alert.get("hostname", "unknown")

    if severity == "P1":
        plan = [
            {"step": 1, "action": f"Isolate host {hostname}", "action_type": "isolate_host", "target": hostname, "reasoning": "Containment required for P1 incident", "urgency": "immediate", "requires_approval": True},
            {"step": 2, "action": "Block identified IOCs", "action_type": "block_ioc", "target": alert.get("dest_ip", "unknown"), "reasoning": "Cut off malicious communication", "urgency": "immediate", "requires_approval": True},
            {"step": 3, "action": "Escalate to Tier 3", "action_type": "escalate", "target": "Tier 3 IR Team", "reasoning": "P1 requires senior investigation", "urgency": "immediate", "requires_approval": True},
            {"step": 4, "action": "Create critical incident ticket", "action_type": "create_ticket", "target": "ITSM", "reasoning": "Audit trail and documentation", "urgency": "immediate", "requires_approval": False},
        ]
    elif severity == "P2":
        plan = [
            {"step": 1, "action": "Block identified IOCs", "action_type": "block_ioc", "target": alert.get("dest_ip", "unknown"), "reasoning": "Reduce exposure during investigation", "urgency": "within_30min", "requires_approval": True},
            {"step": 2, "action": "Create high-priority ticket", "action_type": "create_ticket", "target": "ITSM", "reasoning": "Assign for investigation", "urgency": "within_30min", "requires_approval": False},
            {"step": 3, "action": "Escalate to Tier 2", "action_type": "escalate", "target": "Tier 2", "reasoning": "Needs skilled analysis", "urgency": "within_30min", "requires_approval": True},
        ]
    elif severity == "P3":
        plan = [
            {"step": 1, "action": "Create medium-priority ticket", "action_type": "create_ticket", "target": "ITSM", "reasoning": "Queue for analyst review", "urgency": "within_24hrs", "requires_approval": False},
            {"step": 2, "action": "Add IOCs to watchlist", "action_type": "add_to_watchlist", "target": alert.get("dest_ip", "unknown"), "reasoning": "Monitor for recurrence", "urgency": "within_24hrs", "requires_approval": False},
        ]
    else:  # P4
        plan = [
            {"step": 1, "action": "Create low-priority ticket", "action_type": "create_ticket", "target": "ITSM", "reasoning": "Audit trail and record-keeping", "urgency": "when_convenient", "requires_approval": False},
        ]

    return {
        "alert_id": alert_id,
        "severity_acted_on": severity,
        "was_adjusted": verdict.get("decision") == "adjusted",
        "response_plan": plan,
        "incident_summary": f"Default response plan generated for {severity} alert {alert_id} on {hostname}.",
        "analyst_notes": "LLM response parsing failed. This is a default plan — manual review recommended.",
        "estimated_mttr_impact": "Default plan provides basic coverage. Manual refinement recommended.",
    }
