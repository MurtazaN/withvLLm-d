"""FastAPI Guard configuration for SOC-Claw.

Network-layer WAF that runs as the outermost middleware. Rejects bad
IPs, rate-limited callers, and previously-banned offenders before any
session / CSRF / handler work.

All knobs are env-driven so the same image runs in dev, compose, and
k8s unchanged. Guard's defaults already enable security headers (HSTS,
X-Frame-Options, etc.) and pen-test detection, so we don't pass those.
"""

import os

from guard import SecurityConfig

DEFAULT_WHITELIST = "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
)


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_csp_header() -> str:
    return os.environ.get("SOC_CLAW_CSP", DEFAULT_CSP)


def build_security_config() -> SecurityConfig:
    """Read env vars and return a SecurityConfig for SecurityMiddleware.

    Redis is opt-in via ``SOC_CLAW_REDIS_URL``. When unset (default),
    Guard runs against its in-memory store — single-worker safe. Set
    the env var to a ``redis://`` URL when scaling to multi-worker
    uvicorn or multi-pod k8s.
    """
    whitelist = _parse_csv(
        os.environ.get("SOC_CLAW_IP_WHITELIST", DEFAULT_WHITELIST)
    )
    redis_url = os.environ.get("SOC_CLAW_REDIS_URL", "").strip()
    return SecurityConfig(
        enable_rate_limiting=True,
        rate_limit=int(os.environ.get("SOC_CLAW_RATE_LIMIT", "200")),
        rate_limit_window=int(os.environ.get("SOC_CLAW_RATE_WINDOW", "60")),
        enable_ip_banning=True,
        auto_ban_threshold=int(os.environ.get("SOC_CLAW_AUTO_BAN_THRESHOLD", "20")),
        auto_ban_duration=int(os.environ.get("SOC_CLAW_AUTO_BAN_DURATION", "3600")),
        whitelist=whitelist or None,
        enforce_https=False,
        enable_redis=bool(redis_url),
        redis_url=redis_url or "redis://localhost:6379",
    )
