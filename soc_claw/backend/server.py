"""SOC-Claw FastAPI Backend.

Serves the HTML UI from soc_claw/frontend/templates/index.html and exposes
JSON + SSE endpoints for the pipeline. The benchmark "run all" path
streams per-alert progress over Server-Sent Events so the analyst sees
results as they arrive instead of waiting for the whole batch.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from soc_claw.pipeline import (
    run_pipeline,
    execute_approved_action,
    load_alerts,
    get_alert_by_id,
)
from soc_claw.utils import log_analyst_action

logger = logging.getLogger("soc-claw.server")

app = FastAPI(title="SOC-Claw")

TEMPLATES_DIR = Path(__file__).parent.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ──────────────────────── Pages ────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "alerts": load_alerts(),
    })


# ──────────────────────── API ────────────────────────

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
        for key in ("triage_result", "verification_result", "response_plan"):
            if result.get(key) and isinstance(result[key], dict):
                result[key].pop("_raw_response", None)
        return result
    except Exception as e:
        logger.exception("api_run failed for %s", alert_id)
        return JSONResponse({"error": str(e)}, status_code=500)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _RunAllAggregator:
    """Accumulator for per-alert rows from the run-all stream.

    Owns the running counters, error count, and final summary build so the
    SSE stream loop stays a thin sequence of yields.
    """

    def __init__(self) -> None:
        self.results: list[dict] = []
        self.counts: dict[str, int] = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
        self.triage_correct = 0
        self.verified_correct = 0
        self.errors = 0

    def add(self, row: dict) -> None:
        self.results.append(row)
        verified = row.get("verified")
        if verified in self.counts:
            self.counts[verified] += 1
        if row.get("triage") == row.get("ground_truth"):
            self.triage_correct += 1
        if row.get("correct"):
            self.verified_correct += 1
        if row.get("triage") == "ERROR":
            self.errors += 1

    def summary(self, total: int, elapsed: float) -> dict:
        # Sort embedded results so the final table renders in stable
        # alert-id order regardless of completion order.
        self.results.sort(key=lambda r: r["alert_id"])
        pct = (lambda x: round(x / total * 100, 1)) if total else (lambda _: 0)
        return {
            "total": total,
            "elapsed_s": round(elapsed, 1),
            "counts": self.counts,
            "triage_accuracy": pct(self.triage_correct),
            "verified_accuracy": pct(self.verified_correct),
            "improvement": pct(self.verified_correct - self.triage_correct),
            "errors": self.errors,
            "results": self.results,
        }


async def _process_alert_for_stream(alert: dict, sem: asyncio.Semaphore) -> dict:
    """Run one alert through the pipeline under a concurrency semaphore.

    Returns the row dict the SSE stream will emit. Errors are converted
    into an `ERROR` row rather than propagating, so a single bad alert
    doesn't terminate the whole stream.
    """
    gt_sev = alert["ground_truth"]["severity"]
    async with sem:
        try:
            result = await run_pipeline(alert)
            triage_sev = result["triage_result"].get("severity", "P3")
            if result.get("was_flagged"):
                verified_sev = triage_sev
            else:
                verified_sev = result["final_verdict"].get("verified_severity", triage_sev)
            return {
                "alert_id": alert["id"],
                "ground_truth": gt_sev,
                "triage": triage_sev,
                "verified": verified_sev,
                "correct": verified_sev == gt_sev,
                "decision": result["verification_result"].get("decision", "unknown"),
                "latency_ms": result["timing"]["total_ms"],
            }
        except Exception as e:
            logger.exception("run-all failed on %s", alert["id"])
            return {
                "alert_id": alert["id"],
                "ground_truth": gt_sev,
                "triage": "ERROR",
                "verified": "ERROR",
                "correct": False,
                "decision": "error",
                "latency_ms": 0,
                "error": str(e),
            }


@app.get("/api/run-all")
async def api_run_all():
    """Stream per-alert progress as Server-Sent Events.

    Alerts run concurrently up to `SOC_CLAW_CONCURRENCY` (default 5). The
    browser opens an EventSource() and receives three event types:
      - `start`:   once, before any work starts. Carries `total` and
                   `concurrency`.
      - `result`:  per-alert, in completion order (NOT alert-id order).
                   Carries the same row shape as the final summary plus a
                   `completed` counter.
      - `summary`: final aggregate (counts, accuracy, full results list,
                   sorted by alert_id for stable display).
    """
    alerts = load_alerts()
    total = len(alerts)
    concurrency = max(1, int(os.environ.get("SOC_CLAW_CONCURRENCY", "5")))

    async def stream():
        agg = _RunAllAggregator()
        started_at = time.perf_counter()
        yield _sse("start", {"total": total, "concurrency": concurrency})

        sem = asyncio.Semaphore(concurrency)
        tasks = [
            asyncio.create_task(_process_alert_for_stream(a, sem))
            for a in alerts
        ]
        completed = 0
        for fut in asyncio.as_completed(tasks):
            row = await fut
            completed += 1
            agg.add(row)
            yield _sse("result", {**row, "completed": completed})

        elapsed = time.perf_counter() - started_at
        yield _sse("summary", agg.summary(total, elapsed))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            # Disable proxy buffering so events are flushed promptly.
            # Required behind nginx (`proxy_buffering off`); harmless otherwise.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/approve")
async def api_approve(request: Request):
    """Execute an approved action.

    The client sends `severity` (the plan's `severity_acted_on`) so the
    response_tools can derive ticket priority correctly. Without this,
    `pipeline.execute_approved_action` falls through to a default of "P3"
    (medium) regardless of the real alert severity — see CODE_REVIEW B1.
    """
    body = await request.json()
    action = body.get("action", {})
    alert = body.get("alert", {})
    severity = body.get("severity")
    if severity:
        action["_severity"] = severity
    try:
        return execute_approved_action(action, alert)
    except Exception as e:
        logger.exception("api_approve failed")
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

    from soc_claw.agents.response_agent import run_response
    final_verdict = {
        "decision": "adjusted",
        "original_severity": severity,
        "verified_severity": severity,
        "severity": severity,
        "confidence_in_verification": 100,
        "reasoning": f"Analyst manually overrode severity to {severity}.",
        "issues_found": ["analyst_override"],
        "checks_passed": [],
        "checks_failed": [],
        "recommendation": f"Analyst override to {severity}. Generate response plan accordingly.",
        "was_adjusted": True,
        "was_flagged": False,
    }
    try:
        resp = await run_response(alert, final_verdict)
        resp.pop("_raw_response", None)
        return resp
    except Exception as e:
        logger.exception("api_override failed")
        return JSONResponse({"error": str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=7860)
