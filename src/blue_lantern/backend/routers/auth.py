import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from blue_lantern.backend.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    authenticate,
    create_session,
    destroy_session,
    get_current_user,
)

logger = logging.getLogger("blue-lantern.server.auth")
router = APIRouter(tags=["auth"])

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
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


@router.post("/logout")
async def logout(request: Request):
    sid = request.cookies.get(SESSION_COOKIE)
    username = get_current_user(request) or "unknown"
    if sid:
        destroy_session(sid)
    logger.info("User %s logged out", username)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response
