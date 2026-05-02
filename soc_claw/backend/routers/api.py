import asyncio
import json
import logging
import os
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from soc_claw.audit import log_analyst_action
from soc_claw.pipeline import (
    execute_approved_action,
    get_alert_by_id,
    load_alerts,
    run_pipeline,
)
from soc_claw.agents.response_agent import run_response

logger = logging.getLogger("soc-claw.server.api")
router = APIRouter(prefix="/api", tags=["api"])


@router.get("/alerts")
async def api_alerts():
    return load_alerts()


@router.get("/alerts/{alert_id}")
async def api_alert(alert_id: str):
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)
    return alert


@router.post("/run/{alert_id}")
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


@router.get("/run-all")
async def api_run_all():
    alerts = load_alerts()
    total = len(alerts)
    concurrency = max(1, int(os.environ.get("SOC_CLAW_CONCURRENCY", "5")))

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
    body = await request.json()
    alert_id = body.get("alert_id")
    severity = body.get("severity")
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)

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
