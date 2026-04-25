"""
SOC-Claw Analyst Interface

Gradio-based 4-panel layout for alert triage, verification, response planning,
and action approval with analyst steering.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gradio as gr

from pipeline import (
    run_pipeline,
    execute_approved_action,
    load_alerts,
    get_alert_by_id,
)
from utils import log_analyst_action

# ──────────────────────── Helpers ────────────────────────

SEVERITY_COLORS = {"P1": "#dc2626", "P2": "#ea580c", "P3": "#ca8a04", "P4": "#16a34a"}
DECISION_COLORS = {"confirmed": "#16a34a", "adjusted": "#ea580c", "flagged": "#dc2626"}
URGENCY_COLORS = {
    "immediate": "#dc2626",
    "within_30min": "#ea580c",
    "within_24hrs": "#ca8a04",
    "when_convenient": "#16a34a",
}


def severity_badge(sev: str) -> str:
    color = SEVERITY_COLORS.get(sev, "#6b7280")
    return f'<span style="background:{color};color:white;padding:4px 12px;border-radius:6px;font-weight:bold;font-size:1.3em">{sev}</span>'


def decision_badge(dec: str) -> str:
    color = DECISION_COLORS.get(dec, "#6b7280")
    label = dec.upper()
    return f'<span style="background:{color};color:white;padding:4px 12px;border-radius:6px;font-weight:bold">{label}</span>'


def urgency_badge(urg: str) -> str:
    color = URGENCY_COLORS.get(urg, "#6b7280")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:0.85em">{urg}</span>'


def format_enrichment(triage: dict) -> str:
    """Format enrichment data as HTML cards."""
    tool_calls = triage.get("_tool_calls", [])
    sections = []

    for tc in tool_calls:
        tool = tc["tool"]
        output = tc["output"]
        if tool == "ip_reputation":
            score = output.get("threat_score", 0)
            score_color = "#dc2626" if score >= 80 else "#ea580c" if score >= 40 else "#16a34a"
            tags = ", ".join(output.get("tags", [])) or "None"
            campaigns = ", ".join(output.get("campaigns", [])) or "None"
            verdict = output.get("verdict", "unknown")
            sections.append(f"""
**IP Reputation** ({tc['input'].get('ip', '')})
- Threat Score: <span style="color:{score_color};font-weight:bold">{score}/100</span> — {verdict}
- Tags: {tags}
- Campaigns: {campaigns}
""")
        elif tool == "asset_lookup":
            crit = output.get("criticality", "unknown")
            crit_colors = {"critical": "#dc2626", "high": "#ea580c", "medium": "#ca8a04", "low": "#16a34a"}
            cc = crit_colors.get(crit, "#6b7280")
            sections.append(f"""
**Asset Info** ({output.get('hostname', '')})
- Criticality: <span style="color:{cc};font-weight:bold">{crit.upper()}</span>
- Function: {output.get('business_function', 'N/A')}
- Owner: {output.get('owner', 'N/A')}
- OS: {output.get('os', 'N/A')}
- Network Zone: {output.get('network_zone', 'N/A')}
""")
        elif tool == "mitre_lookup":
            if isinstance(output, list):
                techs = output
            else:
                techs = [output] if output else []
            lines = []
            for t in techs:
                lines.append(f"  - **{t.get('technique_id', '')}** {t.get('name', '')} ({t.get('tactic', '')}) — score: {t.get('match_score', 0):.2f}")
            sections.append("**MITRE ATT&CK Mapping**\n" + "\n".join(lines) if lines else "**MITRE ATT&CK Mapping**: No matches")

    return "\n\n---\n\n".join(sections) if sections else "No enrichment data"


def format_response_plan(plan_data: dict) -> str:
    """Format response plan as HTML."""
    if not plan_data:
        return "No response plan (alert may be flagged for review)"

    plan = plan_data.get("response_plan", [])
    if not plan:
        return "Empty response plan"

    lines = [f"### Recommended Response Plan\n"]
    lines.append(f"**Severity acted on:** {severity_badge(plan_data.get('severity_acted_on', '?'))}")
    if plan_data.get("was_adjusted"):
        lines.append("*(Severity was adjusted by Verifier)*\n")
    lines.append("")

    for step in plan:
        step_num = step.get("step", "?")
        action = step.get("action", "")
        urg = step.get("urgency", "")
        approval = "Requires Approval" if step.get("requires_approval") else "Auto-approved"
        approval_color = "#dc2626" if step.get("requires_approval") else "#16a34a"
        lines.append(
            f"**Step {step_num}: {action}**\n"
            f"- Type: `{step.get('action_type', '')}`\n"
            f"- Target: `{step.get('target', '')}`\n"
            f"- Reasoning: {step.get('reasoning', '')}\n"
            f"- Urgency: {urgency_badge(urg)}\n"
            f"- <span style='color:{approval_color}'>{approval}</span>\n"
        )

    if plan_data.get("incident_summary"):
        lines.append(f"\n---\n**Incident Summary:** {plan_data['incident_summary']}")
    if plan_data.get("analyst_notes"):
        lines.append(f"\n**Analyst Notes:** {plan_data['analyst_notes']}")

    return "\n".join(lines)


def format_verification(verif: dict) -> str:
    """Format verification result as HTML."""
    if not verif:
        return "No verification data"

    decision = verif.get("decision", "unknown")
    lines = [f"### Verification Decision: {decision_badge(decision)}\n"]

    orig = verif.get("original_severity", "?")
    verified = verif.get("verified_severity", "?")

    if decision == "adjusted":
        lines.append(f"**Severity Change:** {severity_badge(orig)} ➜ {severity_badge(verified)}\n")
    elif decision == "flagged":
        lines.append("**Status:** Awaiting analyst review\n")
    else:
        lines.append(f"**Verified Severity:** {severity_badge(verified)}\n")

    lines.append(f"**Confidence:** {verif.get('confidence_in_verification', '?')}%\n")
    lines.append(f"**Reasoning:** {verif.get('reasoning', 'N/A')}\n")

    issues = verif.get("issues_found", [])
    if issues:
        lines.append("**Issues Found:**")
        for issue in issues:
            lines.append(f"- {issue}")

    passed = verif.get("checks_passed", [])
    failed = verif.get("checks_failed", [])
    lines.append("\n**Verification Checklist:**")
    for check in passed:
        lines.append(f"- {check} ✓")
    for check in failed:
        lines.append(f"- {check} ✗")

    if verif.get("recommendation"):
        lines.append(f"\n**Recommendation:** {verif['recommendation']}")

    return "\n".join(lines)


# ──────────────────────── Event Handlers ────────────────────────

def load_alert_json(alert_id: str):
    """Load and display an alert."""
    alert = get_alert_by_id(alert_id)
    if alert:
        return json.dumps(alert, indent=2)
    return "Alert not found"


def run_pipeline_sync(alert_id, steering_text, state):
    """Run the full pipeline synchronously (Gradio wrapper)."""
    alert = get_alert_by_id(alert_id)
    if not alert:
        return (
            "Alert not found",
            "Select an alert first",
            "Select an alert first",
            "Select an alert first",
            "",
            state,
        )

    steering = steering_text.strip() if steering_text else None

    try:
        result = asyncio.run(run_pipeline(alert, steering))
    except Exception as e:
        error_msg = f"Pipeline error: {str(e)}"
        return (
            json.dumps(alert, indent=2),
            error_msg,
            error_msg,
            error_msg,
            f"Error: {e}",
            state,
        )

    # Format outputs
    triage = result.get("triage_result", {})
    verif = result.get("verification_result", {})
    resp = result.get("response_plan")
    timing = result.get("timing", {})

    # Triage column
    triage_md = f"### Triage Result\n\n"
    triage_md += f"**Severity:** {severity_badge(triage.get('severity', '?'))}\n\n"
    triage_md += f"**Confidence:** {triage.get('confidence', '?')}%\n\n"
    triage_md += f"**Reasoning:** {triage.get('reasoning', 'N/A')}\n\n"
    triage_md += f"**Urgency:** {triage.get('recommended_urgency', 'N/A')}\n\n"
    triage_md += "---\n\n"
    triage_md += format_enrichment(triage)

    # Verification column
    verif_md = format_verification(verif)

    # Response column
    resp_md = format_response_plan(resp)

    # Timing bar
    timing_md = (
        f"Triage: {timing.get('triage_ms', 0)/1000:.1f}s → "
        f"Verify: {timing.get('verification_ms', 0)/1000:.1f}s → "
        f"Plan: {timing.get('response_ms', 0)/1000:.1f}s = "
        f"**Total: {timing.get('total_ms', 0)/1000:.1f}s**"
    )

    # Privacy routing
    routes = []
    if triage.get("_route"):
        routes.append(f"Triage: {triage['_route']}")
    if verif.get("_route"):
        routes.append(f"Verifier: {verif['_route']}")
    if resp and resp.get("_route"):
        routes.append(f"Response: {resp['_route']}")
    timing_md += f"  |  Routing: {', '.join(routes)}"

    # Update state
    new_state = {
        "result": result,
        "alert_id": alert_id,
        "action_status": {},
    }

    return (
        json.dumps(alert, indent=2),
        triage_md,
        verif_md,
        resp_md,
        timing_md,
        new_state,
    )


def approve_action(step_num, state):
    """Approve and execute a single response plan step."""
    if not state or "result" not in state:
        return "No pipeline result. Run an alert first.", state

    result = state["result"]
    resp = result.get("response_plan", {})
    plan = resp.get("response_plan", []) if resp else []
    alert = result.get("alert", {})

    step_idx = int(step_num) - 1
    if step_idx < 0 or step_idx >= len(plan):
        return f"Invalid step number: {step_num}", state

    action = plan[step_idx]
    action["_severity"] = resp.get("severity_acted_on", "P3")

    try:
        exec_result = execute_approved_action(action, alert)
        state["action_status"][str(step_num)] = {"status": "executed", "result": exec_result}
        return f"**Step {step_num} Executed** ✓\n```json\n{json.dumps(exec_result, indent=2)}\n```", state
    except Exception as e:
        state["action_status"][str(step_num)] = {"status": "error", "error": str(e)}
        return f"**Step {step_num} Failed** ✗: {e}", state


def reject_action(step_num, state):
    """Reject a response plan step."""
    if not state:
        return "No pipeline result.", state
    state.setdefault("action_status", {})[str(step_num)] = {"status": "rejected"}
    alert_id = state.get("alert_id", "unknown")
    log_analyst_action(alert_id, "reject", f"Step {step_num}")
    return f"**Step {step_num} Rejected** ✗", state


def approve_all(state):
    """Approve all steps requiring approval."""
    if not state or "result" not in state:
        return "No pipeline result.", state

    result = state["result"]
    resp = result.get("response_plan", {})
    plan = resp.get("response_plan", []) if resp else []
    alert = result.get("alert", {})

    logs = []
    for step in plan:
        step_num = step.get("step", 0)
        if step.get("requires_approval"):
            step["_severity"] = resp.get("severity_acted_on", "P3")
            try:
                exec_result = execute_approved_action(step, alert)
                state.setdefault("action_status", {})[str(step_num)] = {"status": "executed", "result": exec_result}
                logs.append(f"Step {step_num}: Executed ✓")
            except Exception as e:
                logs.append(f"Step {step_num}: Failed ✗ ({e})")
        else:
            logs.append(f"Step {step_num}: Auto-approved (no approval needed)")

    return "\n".join(logs), state


def reject_all(state):
    """Reject all steps."""
    if not state or "result" not in state:
        return "No pipeline result.", state

    resp = state["result"].get("response_plan", {})
    plan = resp.get("response_plan", []) if resp else []
    alert_id = state.get("alert_id", "unknown")

    for step in plan:
        step_num = step.get("step", 0)
        state.setdefault("action_status", {})[str(step_num)] = {"status": "rejected"}
    log_analyst_action(alert_id, "reject_all", f"{len(plan)} steps")
    return f"All {len(plan)} steps rejected ✗", state


def override_severity(override_sev, state):
    """Override severity for a flagged alert and run response agent."""
    if not state or "result" not in state:
        return "No pipeline result.", "Override requires a flagged alert.", state

    result = state["result"]
    if not result.get("was_flagged"):
        return "Alert is not flagged — no override needed.", format_response_plan(result.get("response_plan")), state

    alert = result["alert"]
    final_verdict = result.get("final_verdict", {})
    final_verdict["verified_severity"] = override_sev
    final_verdict["was_flagged"] = False
    final_verdict["pending_review"] = False

    alert_id = alert.get("id", "unknown")
    log_analyst_action(alert_id, "override", f"Set severity to {override_sev}")

    try:
        resp = asyncio.run(run_response(alert, final_verdict))
        result["response_plan"] = resp
        result["was_flagged"] = False
        state["result"] = result
        return f"Severity overridden to {override_sev}", format_response_plan(resp), state
    except Exception as e:
        return f"Override failed: {e}", "Error generating response plan", state


def run_all_alerts():
    """Run all 30 alerts through the pipeline and return severity summary."""
    alerts = load_alerts()
    total = len(alerts)
    counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    gt_counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    triage_correct = 0
    verified_correct = 0
    errors = 0
    results_detail = []

    import time as _time
    start = _time.perf_counter()

    for i, alert in enumerate(alerts):
        progress = f"**Processing {i+1}/{total}** — {alert['id']}..."
        yield progress

        gt_sev = alert["ground_truth"]["severity"]
        gt_counts[gt_sev] = gt_counts.get(gt_sev, 0) + 1

        try:
            result = asyncio.run(run_pipeline(alert))
            triage_sev = result["triage_result"].get("severity", "P3")
            if result.get("was_flagged"):
                verified_sev = triage_sev
            else:
                verified_sev = result["final_verdict"].get("verified_severity", triage_sev)

            counts[verified_sev] = counts.get(verified_sev, 0) + 1
            if triage_sev == gt_sev:
                triage_correct += 1
            if verified_sev == gt_sev:
                verified_correct += 1

            results_detail.append(f"| {alert['id']} | {gt_sev} | {triage_sev} | {verified_sev} | {'✓' if verified_sev == gt_sev else '✗'} |")
        except Exception as e:
            errors += 1
            counts["P3"] = counts.get("P3", 0) + 1
            results_detail.append(f"| {alert['id']} | {gt_sev} | ERROR | ERROR | ✗ |")

    elapsed = _time.perf_counter() - start
    processed = total - errors

    triage_acc = triage_correct / total * 100 if total else 0
    verified_acc = verified_correct / total * 100 if total else 0
    improvement = verified_acc - triage_acc

    summary = f"""## Run All Complete — {total} Alerts Processed in {elapsed:.1f}s

### Severity Distribution (Verified)

| Severity | Count | Percentage |
|----------|-------|------------|
| **P1 Critical** | {counts.get('P1', 0)} | {counts.get('P1', 0)/total*100:.0f}% |
| **P2 High** | {counts.get('P2', 0)} | {counts.get('P2', 0)/total*100:.0f}% |
| **P3 Medium** | {counts.get('P3', 0)} | {counts.get('P3', 0)/total*100:.0f}% |
| **P4 Low** | {counts.get('P4', 0)} | {counts.get('P4', 0)/total*100:.0f}% |

### Accuracy

| Metric | Value |
|--------|-------|
| Triage accuracy (before verification) | **{triage_acc:.1f}%** ({triage_correct}/{total}) |
| Verified accuracy (after verification) | **{verified_acc:.1f}%** ({verified_correct}/{total}) |
| Improvement from Verifier | **{improvement:+.1f}%** |
| Errors | {errors} |

### Per-Alert Results

| Alert | Ground Truth | Triage | Verified | Correct |
|-------|-------------|--------|----------|---------|
""" + "\n".join(results_detail)

    yield summary


# ──────────────────────── Build UI ────────────────────────

def create_app():
    alerts = load_alerts()
    alert_ids = [a["id"] for a in alerts]

    with gr.Blocks(
        title="SOC-Claw: Multi-Agent Incident Response",
        theme=gr.themes.Soft(),
    ) as app:
        gr.Markdown("# SOC-Claw: Multi-Agent Incident Response Coordinator")
        gr.Markdown("*Three-agent pipeline: Triage (tools) → Verifier (QA) → Response (plan) → Analyst approves*")

        state = gr.State({})

        # ── Pipeline Timer ──
        timing_display = gr.Markdown("*Run an alert to see pipeline timing*")

        with gr.Row():
            # ── Column 1: Alert Feed ──
            with gr.Column(scale=2):
                gr.Markdown("### Alert Feed")
                alert_dropdown = gr.Dropdown(
                    choices=alert_ids,
                    label="Select Alert",
                    value=alert_ids[0] if alert_ids else None,
                )
                with gr.Row():
                    run_btn = gr.Button("Run Pipeline", variant="primary")
                    auto_feed_btn = gr.Button("Next Alert")
                run_all_btn = gr.Button("Run All 30 Alerts", variant="secondary")
                run_all_output = gr.Markdown("*Click 'Run All 30 Alerts' to process all alerts and see severity summary*")
                alert_json_display = gr.Code(
                    label="Raw Alert JSON",
                    language="json",
                    lines=15,
                )

                # Steering
                gr.Markdown("### Analyst Steering")
                steering_input = gr.Textbox(
                    label="Enter analyst context to re-evaluate",
                    placeholder="e.g., 'This server is in our red team lab'",
                    lines=2,
                )
                steer_btn = gr.Button("Submit Steering")

                # Override for flagged alerts
                gr.Markdown("### Severity Override (flagged alerts)")
                with gr.Row():
                    override_dropdown = gr.Dropdown(
                        choices=["P1", "P2", "P3", "P4"],
                        label="Override Severity",
                        value="P3",
                    )
                    override_btn = gr.Button("Confirm Override")

            # ── Column 2: Triage Results ──
            with gr.Column(scale=3):
                gr.Markdown("### Triage Results")
                triage_display = gr.Markdown("*Waiting for alert...*")

            # ── Column 3: Verification Results ──
            with gr.Column(scale=3):
                gr.Markdown("### Verification Results")
                verif_display = gr.Markdown("*Waiting for triage...*")

            # ── Column 4: Response Plan ──
            with gr.Column(scale=3):
                gr.Markdown("### Response Plan & Actions")
                response_display = gr.Markdown("*Waiting for verification...*")

                gr.Markdown("---")
                gr.Markdown("**Action Controls**")
                with gr.Row():
                    step_input = gr.Number(label="Step #", value=1, minimum=1, maximum=10, precision=0)
                    approve_btn = gr.Button("Approve Step", variant="primary")
                    reject_btn = gr.Button("Reject Step", variant="stop")
                with gr.Row():
                    approve_all_btn = gr.Button("Approve All", variant="primary")
                    reject_all_btn = gr.Button("Reject All", variant="stop")

                execution_log = gr.Markdown("*Execution log will appear here*")

        # ── Event Wiring ──

        # Load alert on dropdown change
        alert_dropdown.change(
            fn=load_alert_json,
            inputs=[alert_dropdown],
            outputs=[alert_json_display],
        )

        # Run pipeline
        run_btn.click(
            fn=run_pipeline_sync,
            inputs=[alert_dropdown, steering_input, state],
            outputs=[alert_json_display, triage_display, verif_display, response_display, timing_display, state],
        )

        # Steering re-run
        steer_btn.click(
            fn=run_pipeline_sync,
            inputs=[alert_dropdown, steering_input, state],
            outputs=[alert_json_display, triage_display, verif_display, response_display, timing_display, state],
        )

        # Auto-feed next alert
        def next_alert(current_id):
            idx = alert_ids.index(current_id) if current_id in alert_ids else -1
            next_idx = (idx + 1) % len(alert_ids)
            return alert_ids[next_idx]

        auto_feed_btn.click(
            fn=next_alert,
            inputs=[alert_dropdown],
            outputs=[alert_dropdown],
        )

        # Run all 30 alerts
        run_all_btn.click(
            fn=run_all_alerts,
            inputs=[],
            outputs=[run_all_output],
        )

        # Action approval
        approve_btn.click(
            fn=approve_action,
            inputs=[step_input, state],
            outputs=[execution_log, state],
        )
        reject_btn.click(
            fn=reject_action,
            inputs=[step_input, state],
            outputs=[execution_log, state],
        )
        approve_all_btn.click(
            fn=approve_all,
            inputs=[state],
            outputs=[execution_log, state],
        )
        reject_all_btn.click(
            fn=reject_all,
            inputs=[state],
            outputs=[execution_log, state],
        )

        # Override
        override_btn.click(
            fn=override_severity,
            inputs=[override_dropdown, state],
            outputs=[execution_log, response_display, state],
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
