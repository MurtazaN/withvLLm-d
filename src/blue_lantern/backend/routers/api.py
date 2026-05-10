import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from blue_lantern.observability.audit import log_analyst_action
from blue_lantern.pipeline import (
    execute_approved_action,
    run_pipeline,
)
from blue_lantern.agents.response_agent import run_response
from blue_lantern.connectors.gcs_reader import download_batch

logger = logging.getLogger("blue-lantern.server.api")
router = APIRouter(prefix="/api", tags=["api"])


@router.get("/alerts")
async def api_alerts(request: Request):
    """Get most recent alerts from GCS."""
    bucket_name = request.app.state.env.get("GCS_LOG_BUCKET_NAME", "")
    if bucket_name:
        alerts = download_batch(bucket_name, max_results=30)
    else:
        alerts = []
    return alerts


@router.get("/alerts/{alert_id}")
async def api_alert(alert_id: str, request: Request):
    """Get alert by ID from GCS."""
    bucket_name = request.app.state.env.get("GCS_LOG_BUCKET_NAME", "")
    if not bucket_name:
        return JSONResponse({"error": "GCS bucket not configured"}, status_code=500)

    from blue_lantern.connectors.gcs_reader import download_alert

    alert = download_alert(bucket_name, alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)

    return alert


@router.post("/process-batch")
async def api_process_batch(request: Request):
    """Process latest N alerts from GCS and return results."""
    bucket_name = request.app.state.env.get("GCS_LOG_BUCKET_NAME", "")
    if not bucket_name:
        return JSONResponse({"error": "GCS bucket not configured"}, status_code=500)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    batch_size = body.get("batch_size", int(os.environ.get("BLUE_LANTERN_BATCH_SIZE", "30")))

    alerts = download_batch(bucket_name, max_results=batch_size)
    results = []

    for alert in alerts:
        try:
            result = await run_pipeline(alert)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process alert {alert.get('id')}: {e}")
            results.append({"error": str(e), "alert_id": alert.get("id")})

    return {"results": results, "count": len(results)}


@router.get("/process-all")
async def api_process_all(request: Request):
    """Process ALL alerts from GCS with real-time SSE streaming."""
    bucket_name = request.app.state.env.get("GCS_LOG_BUCKET_NAME", "")
    if not bucket_name:
        return JSONResponse({"error": "GCS bucket not configured"}, status_code=500)

    alerts = download_batch(bucket_name, max_results=1000)  # Fetch up to 1000 alerts
    total = len(alerts)
    concurrency = max(1, int(os.environ.get("BLUE_LANTERN_CONCURRENCY", "5")))

    async def stream():
        agg = _RunAllAggregator()
        started_at = time.perf_counter()
        yield _format_sse_event("start", {"total": total, "concurrency": concurrency})

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
            yield _format_sse_event("result", {**row, "completed": completed})

        elapsed = time.perf_counter() - started_at
        yield _format_sse_event("summary", agg.summary(total, elapsed))

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/run")
async def api_run(request: Request):
    """Run the pipeline on an alert supplied in the request body.

    The dashboard already holds every visible alert in JS state, so we
    skip the round-trip through GCS — it's slower, costs an extra API
    call per click, and forced an awkward {alert_id} URL contract that
    doesn't survive JSONL files where blob name ≠ alert id.

    Body shape: ``{"alert": {...}, "steering_context": "..."}``.
    """
    body = await request.json()
    alert = body.get("alert")
    if not isinstance(alert, dict):
        return JSONResponse(
            {"error": "Request body must include an 'alert' object"},
            status_code=400,
        )
    steering = body.get("steering_context")

    try:
        result = await run_pipeline(alert, steering)
        for key in ("triage_result", "verification_result", "response_plan"):
            if result.get(key) and isinstance(result[key], dict):
                result[key].pop("_raw_response", None)
        return result
    except Exception as e:
        logger.exception("api_run failed for %s", alert.get("id", "unknown"))
        return JSONResponse({"error": str(e)}, status_code=500)


def _format_sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class _RunAllAggregator:
    """Accumulator for per-alert rows from the run-all stream."""

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
    gt_sev = alert.get("ground_truth", {}).get("severity", "P3")
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


@router.post("/approve")
async def api_approve(request: Request):
    body = await request.json()
    action = body.get("action", {})
    alert = body.get("alert", {})
    severity = body.get("severity")
    if severity:
        action["_severity"] = severity
    analyst = getattr(request.state, "user", "unknown")
    try:
        return execute_approved_action(action, alert, analyst=analyst)
    except Exception as e:
        logger.exception("api_approve failed (analyst=%s)", analyst)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/override")
async def api_override(request: Request):
    """Override an alert's severity using the alert dict from JS state.

    Body shape: ``{"alert": {...}, "severity": "P1|P2|P3|P4"}``.
    Same rationale as ``/api/run``: skip the GCS round-trip; the
    dashboard already has the alert.
    """
    body = await request.json()
    alert = body.get("alert")
    severity = body.get("severity")
    if not isinstance(alert, dict):
        return JSONResponse(
            {"error": "Request body must include an 'alert' object"},
            status_code=400,
        )
    if not severity:
        return JSONResponse(
            {"error": "Request body must include 'severity'"},
            status_code=400,
        )

    alert_id = alert.get("id", "unknown")
    analyst = getattr(request.state, "user", "unknown")
    log_analyst_action(alert_id, "override", f"Set severity to {severity} (by {analyst})")

    final_verdict = {
        "decision": "adjusted",
        "original_severity": severity,
        "verified_severity": severity,
        "severity": severity,
        "confidence_in_verification": 100,
        "reasoning": f"Analyst {analyst} manually overrode severity to {severity}.",
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
