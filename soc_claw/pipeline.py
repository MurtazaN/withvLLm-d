"""
SOC-Claw Pipeline Orchestrator

Agent tool summary:
- Triage Agent: HAS tools (ip_reputation, mitre_lookup, asset_lookup)
- Verifier Agent: NO tools (reasoning only)
- Response Agent: NO tools (recommends actions, doesn't execute them)
- Response tools are called by the UI layer after analyst approval

Flow:
1. Load raw alert from alerts.json (by ID or sequential)
2. Pass alert to Triage Agent
3. Triage Agent calls its tools, produces severity verdict with enrichment data
4. Pass alert + triage verdict to Verifier Agent
5. Verifier evaluates reasoning, returns confirmed/adjusted/flagged
6. If confirmed: use triage verdict as final
7. If adjusted: use verifier's corrected severity as final
8. If flagged: pause pipeline, surface to analyst UI for manual decision
9. Merge triage + verification into final verdict
10. Pass final verdict + original alert to Response Agent
11. Response Agent produces prioritized response plan (no tool calls)
12. Return complete result with all three agent outputs + timing
13. UI displays response plan -> analyst approves/rejects each step ->
    approved steps execute via response_tools.py
"""

import ipaddress
import json
import logging
import time
from pathlib import Path

from soc_claw.agents.triage_agent import run_triage
from soc_claw.agents.verifier_agent import run_verification
from soc_claw.agents.response_agent import run_response
from soc_claw.telemetry import get_tracer
from soc_claw.tools import response_tools
from soc_claw.utils import log_analyst_action

logger = logging.getLogger("soc-claw.pipeline")


def merge_verdict(triage_result: dict, verification_result: dict) -> dict:
    """Merge triage and verification into final verdict.

    If confirmed: use triage severity, keep all enrichment data.
    If adjusted: override severity with verifier's corrected severity,
                 keep all other triage data (enrichment, IOCs, MITRE).
    If flagged: mark as pending_review, do not proceed to response.
    """
    decision = verification_result.get("decision", "confirmed")

    # Start with triage data (minus internal metadata)
    final = {k: v for k, v in triage_result.items() if not k.startswith("_")}

    # Add verification info
    final["verification_decision"] = decision
    final["original_severity"] = triage_result.get("severity", "P3")
    final["verification_reasoning"] = verification_result.get("reasoning", "")
    final["issues_found"] = verification_result.get("issues_found", [])
    final["checks_passed"] = verification_result.get("checks_passed", [])
    final["checks_failed"] = verification_result.get("checks_failed", [])

    if decision == "confirmed":
        final["verified_severity"] = triage_result.get("severity", "P3")
        final["was_adjusted"] = False
        final["was_flagged"] = False
    elif decision == "adjusted":
        final["verified_severity"] = verification_result.get("verified_severity", triage_result.get("severity", "P3"))
        final["was_adjusted"] = True
        final["was_flagged"] = False
    elif decision == "flagged":
        final["verified_severity"] = triage_result.get("severity", "P3")
        final["was_adjusted"] = False
        final["was_flagged"] = True
        final["pending_review"] = True
    else:
        # Unknown decision, treat as confirmed
        final["verified_severity"] = triage_result.get("severity", "P3")
        final["was_adjusted"] = False
        final["was_flagged"] = False

    return final


async def run_pipeline(alert: dict, steering_context: str = None) -> dict:
    """Run full triage -> verify -> response pipeline.

    Returns complete result with all three agent outputs + timing.
    """
    tracer = get_tracer()
    result = {
        "alert": alert,
        "triage_result": None,
        "verification_result": None,
        "final_verdict": None,
        "was_adjusted": False,
        "was_flagged": False,
        "response_plan": None,
        "timing": {},
    }

    with tracer.start_as_current_span(
        "pipeline.run_pipeline",
        attributes={"alert.id": alert.get("id", "unknown")},
    ) as root_span:
        # Stage 1: Triage
        with tracer.start_as_current_span("stage.triage") as triage_span:
            triage_start = time.perf_counter()
            triage_result = await run_triage(alert, steering_context)
            triage_ms = int((time.perf_counter() - triage_start) * 1000)
            triage_span.set_attribute("severity", triage_result.get("severity", ""))
            result["triage_result"] = triage_result
            result["timing"]["triage_ms"] = triage_ms

        # Stage 2: Verification
        with tracer.start_as_current_span("stage.verification") as verify_span:
            verify_start = time.perf_counter()
            verification_result = await run_verification(alert, triage_result, steering_context)
            verify_ms = int((time.perf_counter() - verify_start) * 1000)
            verify_span.set_attribute(
                "decision", verification_result.get("decision", "")
            )
            result["verification_result"] = verification_result
            result["timing"]["verification_ms"] = verify_ms

        # Merge verdict
        final_verdict = merge_verdict(triage_result, verification_result)
        result["final_verdict"] = final_verdict
        result["was_adjusted"] = final_verdict.get("was_adjusted", False)
        result["was_flagged"] = final_verdict.get("was_flagged", False)

        # Stage 3: Response (skip if flagged)
        if final_verdict.get("was_flagged"):
            result["response_plan"] = None
            result["timing"]["response_ms"] = 0
        else:
            with tracer.start_as_current_span("stage.response") as resp_span:
                response_start = time.perf_counter()
                response_result = await run_response(alert, final_verdict, steering_context)
                response_ms = int((time.perf_counter() - response_start) * 1000)
                resp_span.set_attribute(
                    "verified_severity",
                    final_verdict.get("verified_severity", ""),
                )
                result["response_plan"] = response_result
                result["timing"]["response_ms"] = response_ms

        # Total timing
        result["timing"]["total_ms"] = (
            result["timing"]["triage_ms"]
            + result["timing"]["verification_ms"]
            + result["timing"].get("response_ms", 0)
        )

        root_span.set_attribute("was_adjusted", result["was_adjusted"])
        root_span.set_attribute("was_flagged", result["was_flagged"])

    return result


def _classify_indicator(target: str) -> str:
    """Classify a `block_ioc` target as 'ip', 'domain', 'hash', or 'unknown'.

    The previous heuristic (`"." in target and not target[0].isdigit()`)
    mis-classified digit-starting domains like `1password.com` and `911.gov`
    as IPs. `ipaddress.ip_address` validates v4/v6 cleanly; everything else
    falls through to the dot/length checks.
    """
    target = (target or "").strip()
    if not target:
        return "unknown"
    try:
        ipaddress.ip_address(target)
        return "ip"
    except ValueError:
        pass
    if "." in target:
        return "domain"
    if len(target) in (32, 40, 64):  # MD5, SHA1, SHA256
        return "hash"
    return "unknown"


def execute_approved_action(
    action: dict,
    alert: dict = None,
    *,
    analyst: str = "unknown",
) -> dict:
    """Execute an approved response action.

    Called by the UI when an analyst clicks 'Approve' on a response plan step.
    Maps action_type to the corresponding function in response_tools.py.

    The ``analyst`` keyword identifies who approved the action — it lands in
    the audit log so isolation/block/escalation events can be attributed to
    a specific user. Defaults to ``"unknown"`` for callers that don't know
    (e.g. CLI / tests).
    """
    action_type = action.get("action_type", "")
    target = action.get("target", "")
    reasoning = action.get("reasoning", "")
    alert_id = alert.get("id", "unknown") if alert else "unknown"

    log_analyst_action(alert_id, "approve", f"{action_type}: {target} (by {analyst})")

    if action_type == "isolate_host":
        return response_tools.isolate_host(target)
    elif action_type == "block_ioc":
        return response_tools.block_ioc(target, _classify_indicator(target))
    elif action_type == "create_ticket":
        priority_map = {"P1": "critical", "P2": "high", "P3": "medium", "P4": "low"}
        severity = action.get("_severity", "P3")
        priority = priority_map.get(severity, "medium")
        summary = f"[{alert_id}] {action.get('action', target)}"
        return response_tools.create_ticket(summary, priority)
    elif action_type == "escalate":
        tier = 3 if "3" in str(target) or "IR" in str(target) else 2
        return response_tools.escalate(tier, reasoning)
    else:
        # Log-only actions: collect_forensics, add_to_watchlist, notify_owner, tune_rule
        return {
            "status": "logged",
            "action": action_type,
            "target": target,
            "note": f"Action '{action_type}' logged. Requires manual execution outside SOC-Claw.",
        }


def load_alerts() -> list[dict]:
    """Load all alerts from the data directory.

    Each alert is validated against the ``Alert`` schema. Invalid entries
    are logged and skipped so a single bad record doesn't block the
    entire pipeline.
    """
    from soc_claw.schemas import Alert

    data_path = Path(__file__).parent / "data" / "alerts.json"
    with open(data_path) as f:
        raw = json.load(f)

    validated: list[dict] = []
    for i, item in enumerate(raw):
        try:
            alert = Alert.model_validate(item)
            validated.append(alert.model_dump())
        except Exception as exc:
            logger.warning("Skipping invalid alert at index %d: %s", i, exc)
    return validated


def get_alert_by_id(alert_id: str) -> dict | None:
    """Get a single alert by ID."""
    alerts = load_alerts()
    for alert in alerts:
        if alert["id"] == alert_id:
            return alert
    return None
