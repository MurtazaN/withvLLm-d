from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from blue_lantern.connectors.gcs_reader import download_batch

router = APIRouter(tags=["pages"])

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "frontend" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard showing most recent alerts from GCS."""
    # Fetch alerts from GCS
    bucket_name = request.app.state.env.get("GCS_LOG_BUCKET_NAME", "")
    if bucket_name:
        alerts = download_batch(bucket_name, max_results=30)
    else:
        alerts = []

    return templates.TemplateResponse(request, "index.html", context={
        "alerts": alerts,
        "analyst": request.state.user,  # S6: real authenticated username
    })
