"""SOC-Claw Authentication Module.

Provides session-based authentication with bcrypt password hashing.
Users are loaded from the ``SOC_CLAW_USERS`` environment variable or
fall back to a default dev account.

Session state is stored in-memory (dict).  This is correct for
single-process ``uvicorn`` deployments.  For multi-worker / k8s,
swap ``_sessions`` for a Redis-backed store.
"""

import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

import bcrypt
from starlette.requests import Request

logger = logging.getLogger("soc-claw.auth")

# ──────────────────────── Configuration ────────────────────────

SECRET_KEY = os.environ.get("SOC_CLAW_SECRET_KEY", secrets.token_hex(32))
SESSION_COOKIE = "soc_session"
SESSION_MAX_AGE = int(os.environ.get("SOC_CLAW_SESSION_MAX_AGE", 8 * 3600))  # 8h

# ──────────────────────── Session Store ────────────────────────

_sessions: dict[str, dict] = {}


def create_session(username: str) -> str:
    """Create a new session and return its ID."""
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = {
        "username": username,
        "created": datetime.now(timezone.utc),
    }
    return sid


def get_session(sid: str) -> dict | None:
    """Return session data if the session exists and hasn't expired."""
    session = _sessions.get(sid)
    if session is None:
        return None
    age = datetime.now(timezone.utc) - session["created"]
    if age > timedelta(seconds=SESSION_MAX_AGE):
        _sessions.pop(sid, None)
        return None
    return session


def destroy_session(sid: str) -> None:
    """Remove a session."""
    _sessions.pop(sid, None)


def get_current_user(request: Request) -> str | None:
    """Extract the authenticated username from the session cookie.

    Returns ``None`` if the request has no valid session.
    """
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid:
        return None
    session = get_session(sid)
    return session["username"] if session else None


# ──────────────────────── User Store ────────────────────────

_users: dict[str, str] = {}  # {username: bcrypt_hash}


def _hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


def _load_users() -> None:
    """Load users from the ``SOC_CLAW_USERS`` environment variable.

    Format::

        SOC_CLAW_USERS="analyst1:$2b$12$...,analyst2:$2b$12$..."

    If the variable is not set, a default ``analyst`` / ``analyst``
    account is created and a warning is logged.
    """
    global _users
    raw = os.environ.get("SOC_CLAW_USERS", "")
    if raw:
        for entry in raw.split(","):
            entry = entry.strip()
            if ":" not in entry:
                continue
            username, pw_hash = entry.split(":", 1)
            _users[username.strip()] = pw_hash.strip()
        logger.info("Loaded %d user(s) from SOC_CLAW_USERS", len(_users))
    else:
        # Default dev account
        _users["analyst"] = _hash_password("analyst")
        logger.warning(
            "SOC_CLAW_USERS not set — using default dev account "
            "(analyst / analyst). Do NOT use in production."
        )


def authenticate(username: str, password: str) -> bool:
    """Validate credentials. Returns True if username+password match."""
    if not _users:
        _load_users()
    pw_hash = _users.get(username)
    if pw_hash is None:
        return False
    return _verify_password(password, pw_hash)


# ──────────────────────── CLI Helper ────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m soc_claw.backend.auth <password>")
        print("  Generates a bcrypt hash suitable for SOC_CLAW_USERS")
        sys.exit(1)
    pw = sys.argv[1]
    print(f"{_hash_password(pw)}")
