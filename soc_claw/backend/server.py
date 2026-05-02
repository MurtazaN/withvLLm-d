"""SOC-Claw FastAPI Backend.

Serves the HTML UI from soc_claw/frontend/templates/index.html and exposes
JSON + SSE endpoints for the pipeline. The benchmark "run all" path
streams per-alert progress over Server-Sent Events so the analyst sees
results as they arrive instead of waiting for the whole batch.

Authentication (S1):  session-cookie auth via ``soc_claw.backend.auth``.
CSRF protection (S3): ``starlette-csrf`` middleware on all POST endpoints.
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from guard import SecurityMiddleware
from starlette_csrf import CSRFMiddleware

from soc_claw.backend.security import build_csp_header, build_security_config
from soc_claw.logging_config import setup_logging
from soc_claw.telemetry import setup_tracing

# Observability bootstrap. MUST run before FastAPI(...) is constructed
# so FastAPIInstrumentor's class-level patch covers the app instance.
setup_logging()
setup_tracing()

from soc_claw.backend.auth import (  # noqa: E402  (after observability bootstrap)
    SECRET_KEY,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    authenticate,
    create_session,
    destroy_session,
    get_current_user,
)
from soc_claw.guardrails import GuardrailViolation  # noqa: E402
from soc_claw.pipeline import (  # noqa: E402
    run_pipeline,
    execute_approved_action,
    load_alerts,
    get_alert_by_id,
)
from soc_claw.utils import log_analyst_action  # noqa: E402

logger = logging.getLogger("soc-claw.server")

app = FastAPI(title="SOC-Claw")

# ──────────────────────── CSRF Middleware (S3) ────────────────────────
# starlette-csrf sets a ``csrftoken`` cookie and requires a matching
# ``x-csrftoken`` header on POST/PUT/DELETE.  The login endpoint is
# exempt because there is no session to protect before auth.
app.add_middleware(
    CSRFMiddleware,
    secret=SECRET_KEY,
    cookie_name="csrftoken",
    cookie_samesite="lax",
    exempt_urls=[re.compile(r"^/login/?$"), re.compile(r"^/logout/?$")],
)

TEMPLATES_DIR = Path(__file__).parent.parent / "frontend" / "templates"
STATIC_DIR = Path(__file__).parent.parent / "frontend" / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Self-hosted CSS, fonts, and other build artifacts (see Dockerfile stage 1).
# Mounted under /static so the login page can fetch them before auth.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/login/", "/logout", "/logout/"}
_PUBLIC_PREFIXES = ("/static/",)


@app.middleware("http")
async def csp_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = build_csp_header()
    return response


# ──────────────────────── Auth Middleware (S1) ────────────────────────

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Enforce authentication on all non-public routes.

    - Browser requests (non-API) get a 302 redirect to ``/login``.
    - API requests get a 401 JSON response.
    """
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)

    user = get_current_user(request)
    if not user:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Not authenticated"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    # Attach user to request state for downstream use (S6)
    request.state.user = user
    return await call_next(request)


# ───────────────────── FastAPI Guard (S7 + S8) ──────────────────────
# Network-layer WAF: IP whitelist, per-IP rate limiting, auto-banning,
# security headers, attack-pattern detection.
#
# Registered LAST so Starlette's LIFO middleware ordering puts it
# OUTERMOST at runtime — bad traffic is rejected before any session,
# CSRF, or handler work runs. Runtime order:
#   request → SecurityMiddleware → auth_middleware → CSRFMiddleware → handler
app.add_middleware(SecurityMiddleware, config=build_security_config())


# ──────────────────────── Auth Pages ────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))

    if not authenticate(username, password):
        return templates.TemplateResponse(request, "login.html", context={
            "error": "Invalid username or password",
        })

    sid = create_session(username)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        secure=False,  # Set True behind HTTPS reverse proxy
    )
    logger.info("User %s logged in", username)
    return response


@app.post("/logout")
async def logout(request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    username = get_current_user(request) or "unknown"
    if sid:
        destroy_session(sid)
    logger.info("User %s logged out", username)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


# ──────────────────────── Pages ────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", context={
        "alerts": load_alerts(),
        "analyst": request.state.user,  # S6: real authenticated username
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
    into an ``ERROR`` row rather than propagating, so a single bad alert
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

    # Validate client-supplied severity/confidence at the boundary.
    # Severity is clamped to the P1-P4 set; anything else falls back to P3.
    # Confidence is coerced to int and clamped to [0, 100].
    # NOTE: these values are still client-trusted — the larger fix is
    # server-side plan persistence (tracked as S4 in CODE_REVIEW.md). The
    # validation here only prevents type errors and obviously-bogus values.
    severity = str(body.get("severity", "")).upper()
    if severity not in ("P1", "P2", "P3", "P4"):
        severity = "P3"
    action["_severity"] = severity

    raw_conf = body.get("confidence")
    if raw_conf is not None:
        try:
            conf = int(raw_conf)
        except (TypeError, ValueError):
            conf = 0
        action["_confidence"] = max(0, min(100, conf))

    analyst = getattr(request.state, "user", "unknown")
    try:
        return execute_approved_action(action, alert, analyst=analyst)
    except GuardrailViolation as e:
        logger.warning("guardrail_blocked_approve rule=%s analyst=%s", e.rule, analyst)
        return JSONResponse({"error": str(e), "rule": e.rule}, status_code=403)
    except Exception as e:
        logger.exception("api_approve failed (analyst=%s)", analyst)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/override")
async def api_override(request: Request):
    body = await request.json()
    alert_id = body.get("alert_id")
    severity = body.get("severity")
    alert = get_alert_by_id(alert_id)
    if not alert:
        return JSONResponse({"error": "Alert not found"}, status_code=404)

    analyst = getattr(request.state, "user", "unknown")
    log_analyst_action(alert_id, "override", f"Set severity to {severity} (by {analyst})")

    from soc_claw.agents.response_agent import run_response
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
