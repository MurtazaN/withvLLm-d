"""
SOC-Claw FastAPI Backend

Serves the HTML UI and exposes API endpoints for the pipeline.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pipeline import (
    run_pipeline,
    execute_approved_action,
    load_alerts,
    get_alert_by_id,
)
from utils import log_analyst_action

app = FastAPI(title="SOC-Claw")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


# ──────────────────────── Pages ────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    alerts = load_alerts()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "alerts": alerts,
    })


# ──────────────────────── API Endpoints ────────────────────────

@app.get("/api/alerts")
async def api_alerts():
    return load_alerts()


@app.get("/api/alerts/{alert_id}")
async def api_alert(alert_id: str):
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)
    return alert


@app.post("/api/run/{alert_id}")
async def api_run(alert_id: str, request: Request):
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    steering = body.get("steering_context")

    try:
        result = await run_pipeline(alert, steering)
        # Strip _raw_response to reduce payload size
        for key in ("triage_result", "verification_result", "response_plan"):
            if result.get(key) and isinstance(result[key], dict):
                result[key].pop("_raw_response", None)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/run-all")
async def api_run_all():
    alerts = load_alerts()
    results = []
    counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    triage_correct = 0
    verified_correct = 0
    errors = 0

    start = time.perf_counter()
    for alert in alerts:
        gt_sev = alert["ground_truth"]["severity"]
        try:
            result = await run_pipeline(alert)
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

            results.append({
                "alert_id": alert["id"],
                "ground_truth": gt_sev,
                "triage": triage_sev,
                "verified": verified_sev,
                "correct": verified_sev == gt_sev,
                "decision": result["verification_result"].get("decision", "unknown"),
                "latency_ms": result["timing"]["total_ms"],
            })
        except Exception as e:
            errors += 1
            results.append({
                "alert_id": alert["id"],
                "ground_truth": gt_sev,
                "triage": "ERROR",
                "verified": "ERROR",
                "correct": False,
                "decision": "error",
                "latency_ms": 0,
            })

    elapsed = time.perf_counter() - start
    total = len(alerts)

    return {
        "total": total,
        "elapsed_s": round(elapsed, 1),
        "counts": counts,
        "triage_accuracy": round(triage_correct / total * 100, 1) if total else 0,
        "verified_accuracy": round(verified_correct / total * 100, 1) if total else 0,
        "improvement": round((verified_correct - triage_correct) / total * 100, 1) if total else 0,
        "errors": errors,
        "results": results,
    }


@app.post("/api/approve")
async def api_approve(request: Request):
    body = await request.json()
    action = body.get("action", {})
    alert = body.get("alert", {})
    try:
        result = execute_approved_action(action, alert)
        return result
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/override")
async def api_override(request: Request):
    body = await request.json()
    alert_id = body.get("alert_id")
    severity = body.get("severity")
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)

    log_analyst_action(alert_id, "override", f"Set severity to {severity}")

    from agents.response_agent import run_response
    final_verdict = {
        "verified_severity": severity,
        "severity": severity,
        "was_adjusted": True,
        "was_flagged": False,
    }
    try:
        resp = await run_response(alert, final_verdict)
        resp.pop("_raw_response", None)
        return resp
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
