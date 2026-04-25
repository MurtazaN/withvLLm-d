"""
SOC-Claw Analyst Interface — Red Hat themed

Gradio-based multi-panel layout matching the SOC-Claw design system.
"""

import asyncio
import json
import sys
import time as _time
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

# ──────────────────────── Custom CSS ────────────────────────

CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Red+Hat+Display:wght@400;600;700;900&family=Red+Hat+Text:wght@400;500;700&family=Red+Hat+Mono:wght@400;500&display=swap');

.gradio-container {
    font-family: 'Red Hat Text', sans-serif !important;
    background: #fcf9f8 !important;
    max-width: 1600px !important;
}
.soc-header {
    background: #151515;
    color: white;
    padding: 16px 24px;
    border-radius: 8px;
    margin-bottom: 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.soc-header h1 {
    font-family: 'Red Hat Display', sans-serif;
    font-weight: 900;
    font-size: 24px;
    color: #EE0000;
    margin: 0;
}
.soc-header .subtitle {
    color: #71717a;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    margin-top: 2px;
}
.soc-header .status {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #a1a1aa;
    font-size: 11px;
}
.soc-header .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #22c55e;
}
.stat-card {
    background: white;
    border: 1px solid #e4e4e7;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
}
.stat-card .stat-label {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: #71717a;
    font-weight: 700;
    margin-bottom: 8px;
}
.stat-card .stat-value {
    font-family: 'Red Hat Display', sans-serif;
    font-size: 36px;
    font-weight: 900;
    line-height: 1;
}
.stat-card.p1 { border-left: 4px solid #EE0000; }
.stat-card.p1 .stat-value { color: #EE0000; }
.stat-card.p2 { border-left: 4px solid #ea580c; }
.stat-card.p2 .stat-value { color: #ea580c; }
.stat-card.p3 { border-left: 4px solid #2563eb; }
.stat-card.p3 .stat-value { color: #2563eb; }
.stat-card.p4 { border-left: 4px solid #16a34a; }
.stat-card.p4 .stat-value { color: #16a34a; }
.section-card {
    background: white;
    border: 1px solid #e4e4e7;
    border-radius: 8px;
    overflow: hidden;
}
.section-header {
    background: #fafafa;
    border-bottom: 1px solid #f0f0f0;
    padding: 12px 20px;
    font-family: 'Red Hat Display', sans-serif;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    color: #18181b;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-header .icon { color: #EE0000; font-size: 18px; }
.section-body { padding: 20px; }
.sev-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 4px;
    font-weight: 800;
    font-size: 12px;
    text-transform: uppercase;
    color: white;
    letter-spacing: 0.05em;
}
.sev-badge.p1 { background: #EE0000; }
.sev-badge.p2 { background: #ea580c; }
.sev-badge.p3 { background: #2563eb; }
.sev-badge.p4 { background: #16a34a; }
.decision-badge {
    display: inline-block;
    padding: 4px 14px;
    border-radius: 4px;
    font-weight: 800;
    font-size: 11px;
    text-transform: uppercase;
    color: white;
}
.decision-badge.confirmed { background: #16a34a; }
.decision-badge.adjusted { background: #ea580c; }
.decision-badge.flagged { background: #EE0000; }
.urgency-tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 10px;
    font-weight: 700;
    color: white;
    text-transform: uppercase;
}
.urgency-tag.immediate { background: #EE0000; }
.urgency-tag.within_30min { background: #ea580c; }
.urgency-tag.within_24hrs { background: #2563eb; }
.urgency-tag.when_convenient { background: #16a34a; }
.check-item {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: #3f3f46;
    padding: 4px 0;
}
.check-pass { color: #16a34a; font-weight: bold; }
.check-fail { color: #EE0000; font-weight: bold; }
.enrichment-block {
    background: #fafafa;
    border: 1px solid #f0f0f0;
    border-radius: 6px;
    padding: 14px;
    margin-bottom: 12px;
}
.enrichment-block .block-title {
    font-size: 10px;
    text-transform: uppercase;
    font-weight: 700;
    color: #71717a;
    letter-spacing: 0.08em;
    margin-bottom: 8px;
}
.threat-score-bar {
    height: 6px;
    background: #e4e4e7;
    border-radius: 3px;
    overflow: hidden;
    margin-top: 6px;
}
.threat-score-fill {
    height: 100%;
    border-radius: 3px;
}
.step-item {
    position: relative;
    padding-left: 28px;
    padding-bottom: 16px;
    border-left: 2px solid #e4e4e7;
    margin-left: 10px;
}
.step-item:last-child { border-left-color: transparent; }
.step-num {
    position: absolute;
    left: -11px;
    top: 0;
    width: 20px;
    height: 20px;
    border-radius: 50%;
    background: #EE0000;
    color: white;
    font-size: 10px;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
}
.step-action {
    font-size: 14px;
    font-weight: 700;
    color: #18181b;
    margin-bottom: 4px;
}
.step-detail {
    font-size: 12px;
    color: #71717a;
    line-height: 1.5;
}
.timing-bar {
    background: #151515;
    color: #a1a1aa;
    padding: 10px 20px;
    border-radius: 6px;
    font-family: 'Red Hat Mono', monospace;
    font-size: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.timing-bar .timing-total { color: #EE0000; font-weight: 700; }
.run-all-progress {
    background: white;
    border: 1px solid #e4e4e7;
    border-radius: 8px;
    padding: 20px;
}
"""

# ──────────────────────── Formatters ────────────────────────

SEV_CLASS = {"P1": "p1", "P2": "p2", "P3": "p3", "P4": "p4"}


def sev_badge(sev):
    cls = SEV_CLASS.get(sev, "")
    return f'<span class="sev-badge {cls}">{sev}</span>'


def decision_badge_html(dec):
    return f'<span class="decision-badge {dec}">{dec.upper()}</span>'


def urgency_tag(urg):
    cls = urg.replace(" ", "_") if urg else ""
    return f'<span class="urgency-tag {cls}">{urg}</span>'


def format_enrichment_html(triage):
    tool_calls = triage.get("_tool_calls", [])
    html = ""
    for tc in tool_calls:
        tool = tc["tool"]
        out = tc["output"]
        if tool == "ip_reputation":
            score = out.get("threat_score", 0)
            color = "#EE0000" if score >= 80 else "#ea580c" if score >= 40 else "#16a34a"
            tags = ", ".join(out.get("tags", [])) or "None"
            campaigns = ", ".join(out.get("campaigns", [])) or "None"
            html += f'''<div class="enrichment-block">
                <div class="block-title">IP Reputation — {tc["input"].get("ip","")}</div>
                <div style="display:flex;justify-content:space-between;align-items:baseline">
                    <span style="font-size:13px;color:#3f3f46">Verdict: <b>{out.get("verdict","unknown")}</b></span>
                    <span style="font-size:22px;font-weight:900;color:{color}">{score}/100</span>
                </div>
                <div class="threat-score-bar"><div class="threat-score-fill" style="width:{score}%;background:{color}"></div></div>
                <div style="margin-top:8px;font-size:12px;color:#71717a">Tags: {tags}<br>Campaigns: {campaigns}</div>
            </div>'''
        elif tool == "asset_lookup":
            crit = out.get("criticality", "unknown")
            crit_colors = {"critical": "#EE0000", "high": "#ea580c", "medium": "#ca8a04", "low": "#16a34a"}
            cc = crit_colors.get(crit, "#71717a")
            html += f'''<div class="enrichment-block">
                <div class="block-title">Asset Intelligence — {out.get("hostname","")}</div>
                <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:#3f3f46">
                    <div><span style="color:#71717a">Criticality</span><br><span style="color:{cc};font-weight:800;text-transform:uppercase">{crit}</span></div>
                    <div><span style="color:#71717a">Owner</span><br><b>{out.get("owner","N/A")}</b></div>
                    <div><span style="color:#71717a">OS</span><br><b>{out.get("os","N/A")}</b></div>
                    <div><span style="color:#71717a">Zone</span><br><b>{out.get("network_zone","N/A")}</b></div>
                </div>
                <div style="margin-top:6px;font-size:12px;color:#71717a">Function: {out.get("business_function","N/A")}</div>
            </div>'''
        elif tool == "mitre_lookup":
            techs = out if isinstance(out, list) else [out] if out else []
            badges = ""
            for t in techs:
                badges += f'<div style="background:#151515;color:white;padding:6px 12px;border-radius:4px;display:inline-flex;align-items:center;gap:8px;margin:2px"><span style="font-family:Red Hat Mono;font-size:11px;opacity:0.7">{t.get("technique_id","")}</span><span style="font-size:12px;font-weight:700">{t.get("name","")}</span></div> '
            html += f'''<div class="enrichment-block">
                <div class="block-title">MITRE ATT&CK Mapping</div>
                <div style="display:flex;flex-wrap:wrap;gap:4px">{badges if badges else "<span style='color:#71717a;font-size:12px'>No matches</span>"}</div>
            </div>'''
    return html or '<div style="color:#71717a;font-size:13px">No enrichment data yet</div>'


def format_verification_html(verif):
    if not verif:
        return '<div style="color:#71717a;font-size:13px">Awaiting triage result...</div>'
    decision = verif.get("decision", "unknown")
    orig = verif.get("original_severity", "?")
    verified = verif.get("verified_severity", "?")
    confidence = verif.get("confidence_in_verification", "?")
    reasoning = verif.get("reasoning", "N/A")

    html = f'<div style="margin-bottom:16px">{decision_badge_html(decision)}</div>'
    if decision == "adjusted":
        html += f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">{sev_badge(orig)} <span style="font-size:18px;color:#71717a">→</span> {sev_badge(verified)}</div>'
    else:
        html += f'<div style="margin-bottom:12px">{sev_badge(verified)}</div>'

    html += f'<div style="font-size:13px;color:#3f3f46;margin-bottom:12px"><b>Confidence:</b> {confidence}%</div>'
    html += f'<div style="font-size:13px;color:#3f3f46;margin-bottom:16px;font-style:italic">"{reasoning}"</div>'

    issues = verif.get("issues_found", [])
    if issues:
        html += '<div style="margin-bottom:12px"><div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.08em;margin-bottom:6px">Issues Found</div>'
        for issue in issues:
            html += f'<div class="check-item"><span class="check-fail">!</span> {issue}</div>'
        html += '</div>'

    passed = verif.get("checks_passed", [])
    failed = verif.get("checks_failed", [])
    html += '<div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.08em;margin-bottom:6px">Verification Checklist</div>'
    for c in passed:
        html += f'<div class="check-item"><span class="check-pass">✓</span> {c}</div>'
    for c in failed:
        html += f'<div class="check-item"><span class="check-fail">✗</span> {c}</div>'
    return html


def format_response_html(plan_data):
    if not plan_data:
        return '<div style="color:#71717a;font-size:13px">No response plan (alert may be flagged)</div>'
    plan = plan_data.get("response_plan", [])
    if not plan:
        return '<div style="color:#71717a;font-size:13px">Empty response plan</div>'

    sev = plan_data.get("severity_acted_on", "?")
    html = f'<div style="margin-bottom:16px;display:flex;align-items:center;gap:12px">Planning for: {sev_badge(sev)}'
    if plan_data.get("was_adjusted"):
        html += ' <span style="font-size:11px;color:#ea580c;font-weight:700">ADJUSTED</span>'
    html += '</div><div style="margin-top:12px">'

    for step in plan:
        num = step.get("step", "?")
        action = step.get("action", "")
        urg = step.get("urgency", "")
        reasoning = step.get("reasoning", "")
        approval = step.get("requires_approval", False)
        approval_html = '<span style="color:#EE0000;font-size:10px;font-weight:700;text-transform:uppercase">Requires Approval</span>' if approval else '<span style="color:#16a34a;font-size:10px;font-weight:700;text-transform:uppercase">Auto-approved</span>'
        html += f'''<div class="step-item">
            <div class="step-num">{num}</div>
            <div class="step-action">{action}</div>
            <div class="step-detail">{reasoning}<br>{urgency_tag(urg)} {approval_html}</div>
        </div>'''
    html += '</div>'

    if plan_data.get("incident_summary"):
        html += f'''<div class="enrichment-block" style="margin-top:16px">
            <div class="block-title">Incident Summary</div>
            <div style="font-size:13px;color:#3f3f46">{plan_data["incident_summary"]}</div>
        </div>'''
    if plan_data.get("analyst_notes"):
        html += f'''<div class="enrichment-block">
            <div class="block-title">Analyst Notes</div>
            <div style="font-size:13px;color:#3f3f46;font-style:italic">{plan_data["analyst_notes"]}</div>
        </div>'''
    return html


# ──────────────────────── Event Handlers ────────────────────────

def load_alert_json(alert_id):
    alert = get_alert_by_id(alert_id)
    return json.dumps(alert, indent=2) if alert else "Alert not found"


def run_pipeline_sync(alert_id, steering_text, state):
    alert = get_alert_by_id(alert_id)
    if not alert:
        empty = "Select an alert first"
        return ("", empty, empty, empty, "", state)

    steering = steering_text.strip() if steering_text else None
    try:
        result = asyncio.run(run_pipeline(alert, steering))
    except Exception as e:
        err = f"<div style='color:#EE0000'>Pipeline error: {e}</div>"
        return (json.dumps(alert, indent=2), err, err, err, "", state)

    triage = result.get("triage_result", {})
    verif = result.get("verification_result", {})
    resp = result.get("response_plan")
    timing = result.get("timing", {})

    # Triage HTML
    sev = triage.get("severity", "?")
    conf = triage.get("confidence", "?")
    reasoning = triage.get("reasoning", "N/A")
    urg = triage.get("recommended_urgency", "N/A")
    triage_html = f'''
        <div style="display:flex;gap:16px;margin-bottom:16px">
            <div style="background:#fafafa;border:1px solid #f0f0f0;padding:14px;border-radius:6px;flex:1">
                <div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;margin-bottom:4px">Triage Severity</div>
                <div style="font-size:28px;font-weight:900">{sev}</div>
            </div>
            <div style="background:#fafafa;border:1px solid #f0f0f0;padding:14px;border-radius:6px;flex:1">
                <div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;margin-bottom:4px">Confidence</div>
                <div style="font-size:28px;font-weight:900">{conf}%</div>
            </div>
        </div>
        <div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.08em;margin-bottom:6px">Reasoning Path</div>
        <div style="font-size:13px;color:#3f3f46;font-style:italic;margin-bottom:16px">"{reasoning}"</div>
        <div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.08em;margin-bottom:6px">Urgency: {urg}</div>
        <hr style="border:0;border-top:1px solid #f0f0f0;margin:16px 0">
        {format_enrichment_html(triage)}
    '''

    # Verification HTML
    verif_html = format_verification_html(verif)

    # Response HTML
    resp_html = format_response_html(resp)

    # Timing
    timing_html = f'''<div class="timing-bar">
        <span>Triage: {timing.get("triage_ms",0)/1000:.1f}s → Verify: {timing.get("verification_ms",0)/1000:.1f}s → Plan: {timing.get("response_ms",0)/1000:.1f}s</span>
        <span class="timing-total">Total: {timing.get("total_ms",0)/1000:.1f}s</span>
    </div>'''

    new_state = {"result": result, "alert_id": alert_id, "action_status": {}}
    return (json.dumps(alert, indent=2), triage_html, verif_html, resp_html, timing_html, new_state)


def approve_action(step_num, state):
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
        return f'<div style="color:#16a34a;font-weight:700">Step {step_num} Executed ✓</div><pre style="background:#151515;color:#a1a1aa;padding:12px;border-radius:6px;font-size:11px;margin-top:8px">{json.dumps(exec_result, indent=2)}</pre>', state
    except Exception as e:
        return f'<div style="color:#EE0000;font-weight:700">Step {step_num} Failed ✗: {e}</div>', state


def reject_action(step_num, state):
    if not state:
        return "No pipeline result.", state
    state.setdefault("action_status", {})[str(step_num)] = {"status": "rejected"}
    log_analyst_action(state.get("alert_id", "unknown"), "reject", f"Step {step_num}")
    return f'<div style="color:#EE0000;font-weight:700">Step {step_num} Rejected ✗</div>', state


def approve_all(state):
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
                execute_approved_action(step, alert)
                state.setdefault("action_status", {})[str(step_num)] = {"status": "executed"}
                logs.append(f'<div class="check-item"><span class="check-pass">✓</span> Step {step_num}: Executed</div>')
            except Exception as e:
                logs.append(f'<div class="check-item"><span class="check-fail">✗</span> Step {step_num}: {e}</div>')
        else:
            logs.append(f'<div class="check-item"><span style="color:#71717a">—</span> Step {step_num}: Auto-approved</div>')
    return "".join(logs), state


def reject_all(state):
    if not state or "result" not in state:
        return "No pipeline result.", state
    resp = state["result"].get("response_plan", {})
    plan = resp.get("response_plan", []) if resp else []
    for step in plan:
        state.setdefault("action_status", {})[str(step.get("step", 0))] = {"status": "rejected"}
    log_analyst_action(state.get("alert_id", "unknown"), "reject_all", f"{len(plan)} steps")
    return f'<div style="color:#EE0000;font-weight:700">All {len(plan)} steps rejected ✗</div>', state


def override_severity(override_sev, state):
    if not state or "result" not in state:
        return "No pipeline result.", "", state
    result = state["result"]
    if not result.get("was_flagged"):
        return "Alert is not flagged.", format_response_html(result.get("response_plan")), state
    alert = result["alert"]
    final_verdict = result.get("final_verdict", {})
    final_verdict["verified_severity"] = override_sev
    final_verdict["was_flagged"] = False
    log_analyst_action(alert.get("id", "unknown"), "override", f"Set severity to {override_sev}")
    try:
        from agents.response_agent import run_response
        resp = asyncio.run(run_response(alert, final_verdict))
        result["response_plan"] = resp
        result["was_flagged"] = False
        state["result"] = result
        return f'<div style="color:#16a34a">Severity overridden to {override_sev}</div>', format_response_html(resp), state
    except Exception as e:
        return f'<div style="color:#EE0000">Override failed: {e}</div>', "", state


def run_all_alerts():
    alerts = load_alerts()
    total = len(alerts)
    counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    triage_correct = 0
    verified_correct = 0
    errors = 0
    rows = []

    start = _time.perf_counter()
    for i, alert in enumerate(alerts):
        yield f'''<div class="run-all-progress">
            <div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.1em;margin-bottom:8px">Processing Alerts</div>
            <div style="font-size:20px;font-weight:900;color:#18181b;margin-bottom:8px">{i+1} / {total}</div>
            <div style="font-size:13px;color:#71717a">Current: {alert["id"]} — {alert["rule_name"]}</div>
            <div style="background:#e4e4e7;height:6px;border-radius:3px;margin-top:12px;overflow:hidden"><div style="background:#EE0000;height:100%;width:{(i+1)/total*100:.0f}%;border-radius:3px;transition:width 0.3s"></div></div>
        </div>'''

        gt_sev = alert["ground_truth"]["severity"]
        try:
            result = asyncio.run(run_pipeline(alert))
            triage_sev = result["triage_result"].get("severity", "P3")
            verified_sev = result["final_verdict"].get("verified_severity", triage_sev) if not result.get("was_flagged") else triage_sev
            counts[verified_sev] = counts.get(verified_sev, 0) + 1
            if triage_sev == gt_sev: triage_correct += 1
            if verified_sev == gt_sev: verified_correct += 1
            correct = "✓" if verified_sev == gt_sev else "✗"
            color = "#16a34a" if verified_sev == gt_sev else "#EE0000"
            rows.append(f'<tr><td style="padding:6px 12px;font-family:Red Hat Mono;font-size:12px">{alert["id"]}</td><td style="padding:6px 12px">{sev_badge(gt_sev)}</td><td style="padding:6px 12px">{sev_badge(triage_sev)}</td><td style="padding:6px 12px">{sev_badge(verified_sev)}</td><td style="padding:6px 12px;color:{color};font-weight:800">{correct}</td></tr>')
        except Exception as e:
            errors += 1
            counts["P3"] = counts.get("P3", 0) + 1
            rows.append(f'<tr><td style="padding:6px 12px;font-family:Red Hat Mono;font-size:12px">{alert["id"]}</td><td style="padding:6px 12px">{sev_badge(gt_sev)}</td><td style="padding:6px 12px;color:#EE0000">ERR</td><td style="padding:6px 12px;color:#EE0000">ERR</td><td style="padding:6px 12px;color:#EE0000">✗</td></tr>')

    elapsed = _time.perf_counter() - start
    triage_acc = triage_correct / total * 100
    verified_acc = verified_correct / total * 100
    improvement = verified_acc - triage_acc

    summary = f'''
    <div style="margin-bottom:24px">
        <div style="font-family:Red Hat Display;font-size:24px;font-weight:900;color:#18181b;margin-bottom:4px">Benchmark Complete</div>
        <div style="font-size:13px;color:#71717a">{total} alerts processed in {elapsed:.1f}s</div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px">
        <div class="stat-card p1"><div class="stat-label">P1 Critical</div><div class="stat-value">{counts.get("P1",0)}</div></div>
        <div class="stat-card p2"><div class="stat-label">P2 High</div><div class="stat-value">{counts.get("P2",0)}</div></div>
        <div class="stat-card p3"><div class="stat-label">P3 Medium</div><div class="stat-value">{counts.get("P3",0)}</div></div>
        <div class="stat-card p4"><div class="stat-label">P4 Low</div><div class="stat-value">{counts.get("P4",0)}</div></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px">
        <div class="stat-card"><div class="stat-label">Triage Accuracy</div><div class="stat-value" style="color:#18181b">{triage_acc:.0f}%</div></div>
        <div class="stat-card"><div class="stat-label">Verified Accuracy</div><div class="stat-value" style="color:#16a34a">{verified_acc:.0f}%</div></div>
        <div class="stat-card"><div class="stat-label">Improvement</div><div class="stat-value" style="color:{"#16a34a" if improvement >= 0 else "#EE0000"}">{improvement:+.0f}%</div></div>
    </div>
    <div class="section-card">
        <div class="section-header">Per-Alert Results</div>
        <div style="overflow-x:auto">
            <table style="width:100%;border-collapse:collapse;font-size:13px">
                <thead><tr style="background:#fafafa;border-bottom:1px solid #e4e4e7">
                    <th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#71717a;font-weight:700">Alert</th>
                    <th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#71717a;font-weight:700">Ground Truth</th>
                    <th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#71717a;font-weight:700">Triage</th>
                    <th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#71717a;font-weight:700">Verified</th>
                    <th style="padding:10px 12px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:0.1em;color:#71717a;font-weight:700">Match</th>
                </tr></thead>
                <tbody style="divide-y:1px solid #f0f0f0">{"".join(rows)}</tbody>
            </table>
        </div>
    </div>
    '''
    yield summary


# ──────────────────────── Build UI ────────────────────────

def create_app():
    alerts = load_alerts()
    alert_ids = [a["id"] for a in alerts]

    with gr.Blocks(css=CUSTOM_CSS, title="SOC-Claw | Incident Response") as app:
        state = gr.State({})

        # Header
        gr.HTML('''<div class="soc-header">
            <div>
                <h1>SOC-Claw</h1>
                <div class="subtitle">Multi-Agent Incident Response Coordinator</div>
            </div>
            <div class="status">
                <div class="status-dot"></div>
                <span>Pipeline: Triage → Verifier → Response → Analyst Approval</span>
            </div>
        </div>''')

        # Timing bar
        timing_display = gr.HTML('<div class="timing-bar"><span>Run an alert to see pipeline timing</span></div>')

        # Main grid
        with gr.Row():
            # ── Column 1: Controls ──
            with gr.Column(scale=2):
                gr.HTML('<div class="section-card"><div class="section-header"><span class="icon">⚡</span> Alert Feed</div><div class="section-body" id="alert-feed">')
                alert_dropdown = gr.Dropdown(choices=alert_ids, label="Select Alert", value=alert_ids[0] if alert_ids else None)
                with gr.Row():
                    run_btn = gr.Button("Run Pipeline", variant="primary", size="sm")
                    auto_feed_btn = gr.Button("Next Alert", size="sm")
                run_all_btn = gr.Button("Run All 30 Alerts", variant="secondary", size="sm")
                alert_json_display = gr.Code(label="Raw Alert JSON", language="json", lines=12)
                gr.HTML('</div></div>')

                gr.HTML('<div class="section-card" style="margin-top:12px"><div class="section-header"><span class="icon">🧠</span> Analyst Steering</div><div class="section-body">')
                steering_input = gr.Textbox(label="Context", placeholder="e.g., 'This is a red team exercise'", lines=2)
                steer_btn = gr.Button("Submit Steering", size="sm")
                gr.HTML('</div></div>')

                gr.HTML('<div class="section-card" style="margin-top:12px"><div class="section-header"><span class="icon">⚠</span> Severity Override</div><div class="section-body">')
                override_dropdown = gr.Dropdown(choices=["P1", "P2", "P3", "P4"], label="Manual Override", value="P3")
                override_btn = gr.Button("Confirm Override", size="sm")
                gr.HTML('</div></div>')

            # ── Column 2: Triage & Verification ──
            with gr.Column(scale=3):
                gr.HTML('<div class="section-card"><div class="section-header"><span class="icon">🔍</span> Triage & Verification</div><div class="section-body">')
                triage_display = gr.HTML('<div style="color:#71717a;font-size:13px">Select an alert and run the pipeline...</div>')
                gr.HTML('<hr style="border:0;border-top:1px solid #f0f0f0;margin:16px 0">')
                verif_display = gr.HTML('<div style="color:#71717a;font-size:13px">Verification results will appear here...</div>')
                gr.HTML('</div></div>')

            # ── Column 3: Response & Actions ──
            with gr.Column(scale=3):
                gr.HTML('<div class="section-card"><div class="section-header"><span class="icon">🛡</span> Response & Actions</div><div class="section-body">')
                response_display = gr.HTML('<div style="color:#71717a;font-size:13px">Response plan will appear here...</div>')
                gr.HTML('<hr style="border:0;border-top:1px solid #f0f0f0;margin:16px 0"><div style="font-size:10px;text-transform:uppercase;font-weight:700;color:#71717a;letter-spacing:0.1em;margin-bottom:8px">Action Controls</div>')
                with gr.Row():
                    step_input = gr.Number(label="Step #", value=1, minimum=1, maximum=10, precision=0)
                    approve_btn = gr.Button("Approve", variant="primary", size="sm")
                    reject_btn = gr.Button("Reject", variant="stop", size="sm")
                with gr.Row():
                    approve_all_btn = gr.Button("Approve All", variant="primary", size="sm")
                    reject_all_btn = gr.Button("Reject All", variant="stop", size="sm")
                execution_log = gr.HTML('<div style="color:#71717a;font-size:12px;font-style:italic">Execution log...</div>')
                gr.HTML('</div></div>')

        # Run All output
        run_all_output = gr.HTML("")

        # ── Event Wiring ──
        alert_dropdown.change(fn=load_alert_json, inputs=[alert_dropdown], outputs=[alert_json_display])
        run_btn.click(fn=run_pipeline_sync, inputs=[alert_dropdown, steering_input, state], outputs=[alert_json_display, triage_display, verif_display, response_display, timing_display, state])
        steer_btn.click(fn=run_pipeline_sync, inputs=[alert_dropdown, steering_input, state], outputs=[alert_json_display, triage_display, verif_display, response_display, timing_display, state])

        def next_alert(current_id):
            idx = alert_ids.index(current_id) if current_id in alert_ids else -1
            return alert_ids[(idx + 1) % len(alert_ids)]
        auto_feed_btn.click(fn=next_alert, inputs=[alert_dropdown], outputs=[alert_dropdown])

        run_all_btn.click(fn=run_all_alerts, inputs=[], outputs=[run_all_output])
        approve_btn.click(fn=approve_action, inputs=[step_input, state], outputs=[execution_log, state])
        reject_btn.click(fn=reject_action, inputs=[step_input, state], outputs=[execution_log, state])
        approve_all_btn.click(fn=approve_all, inputs=[state], outputs=[execution_log, state])
        reject_all_btn.click(fn=reject_all, inputs=[state], outputs=[execution_log, state])
        override_btn.click(fn=override_severity, inputs=[override_dropdown, state], outputs=[execution_log, response_display, state])

    return app


if __name__ == "__main__":
    app = create_app()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
