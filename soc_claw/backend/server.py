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
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
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
from soc_claw.backend.routers import api_router, auth_router, pages_router
from soc_claw.backend.routes.siem_webhook import router as siem_webhook_router
from soc_claw.backend.routes.batch_api import router as batch_api_router

logger = logging.getLogger("soc-claw.server")

app = FastAPI(title="SOC-Claw")

# Store environment variables in app state for access by routes
app.state.env = {
    "GCS_LOG_BUCKET_NAME": os.environ.get("GCS_LOG_BUCKET_NAME", ""),
    "SOC_CLAW_BATCH_SIZE": int(os.environ.get("SOC_CLAW_BATCH_SIZE", "30")),
    "SOC_CLAW_GCS_POLL_INTERVAL": int(os.environ.get("SOC_CLAW_GCS_POLL_INTERVAL", "300")),
}

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

STATIC_DIR = Path(__file__).parent.parent / "frontend" / "static"

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


# ──────────────────────── Routers ────────────────────────

app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(api_router)
app.include_router(siem_webhook_router)
app.include_router(batch_api_router)


# ──────────────────────── Lifecycle Events ────────────────────────

@app.on_event("startup")
async def startup_event():
    """Start background services on startup."""
    logger.info("Starting SOC-Claw backend")

    # Async Redis client for batch-job tracking. Read by routes via
    # request.app.state.redis; left as None when SOC_CLAW_REDIS_URL is
    # unset, in which case batch endpoints respond 503.
    app.state.redis = None
    redis_url = os.environ.get("SOC_CLAW_REDIS_URL", "").strip()
    if redis_url:
        try:
            from redis.asyncio import Redis
            client = Redis.from_url(redis_url, decode_responses=True)
            await client.ping()
            app.state.redis = client
            logger.info("Redis client connected for batch-job tracking")
        except Exception as e:
            logger.error(f"Failed to connect to Redis ({redis_url}): {e}")

    # Start GCS poller
    try:
        from soc_claw.connectors.gcs_poller import start_gcs_poller
        await start_gcs_poller()
        logger.info("GCS poller started")
    except Exception as e:
        logger.error(f"Failed to start GCS poller: {e}")

    # Start Kafka consumer
    try:
        from soc_claw.connectors.kafka_consumer import start_consumer
        await start_consumer()
        logger.info("Kafka consumer started")
    except Exception as e:
        logger.error(f"Failed to start Kafka consumer: {e}")

    # Start DLQ reprocessor
    try:
        from soc_claw.connectors.dlq_reprocessor import start_dlq_reprocessor
        await start_dlq_reprocessor()
        logger.info("DLQ reprocessor started")
    except Exception as e:
        logger.error(f"Failed to start DLQ reprocessor: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Stop background services on shutdown."""
    logger.info("Stopping SOC-Claw backend")

    # Stop GCS poller
    try:
        from soc_claw.connectors.gcs_poller import stop_gcs_poller
        await stop_gcs_poller()
        logger.info("GCS poller stopped")
    except Exception as e:
        logger.error(f"Failed to stop GCS poller: {e}")

    # Stop Kafka consumer
    try:
        from soc_claw.connectors.kafka_consumer import stop_consumer
        await stop_consumer()
        logger.info("Kafka consumer stopped")
    except Exception as e:
        logger.error(f"Failed to stop Kafka consumer: {e}")

    # Stop DLQ reprocessor
    try:
        from soc_claw.connectors.dlq_reprocessor import stop_dlq_reprocessor
        await stop_dlq_reprocessor()
        logger.info("DLQ reprocessor stopped")
    except Exception as e:
        logger.error(f"Failed to stop DLQ reprocessor: {e}")

    # Close Redis client
    if getattr(app.state, "redis", None):
        try:
            await app.state.redis.aclose()
            logger.info("Redis client closed")
        except Exception as e:
            logger.error(f"Failed to close Redis client: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
