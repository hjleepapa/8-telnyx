"""FastAPI entrypoint: health, telemetry, Telnyx dynamic webhooks, and static site."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from telnyx_restaurant.routers import webhook

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).resolve().parent / "static"
_INDEX = _STATIC / "index.html"
_APP_REV = os.environ.get("RENDER_GIT_COMMIT", os.environ.get("APP_GIT_REVISION", "local"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log deploy fingerprint so Render logs show whether `/` route is from this build."""
    log = logging.getLogger("uvicorn.error")
    log.warning(
        "Hanok Table: rev=%s index_exists=%s app_py=%s",
        _APP_REV[:12] if _APP_REV else "?",
        _INDEX.is_file(),
        Path(__file__).resolve(),
    )
    yield


app = FastAPI(
    title="Telnyx Restaurant Reservation API",
    description="Dynamic webhooks and REST backing store for MCP tools (Telnyx AI challenge).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(webhook.router, prefix="/webhooks/telnyx", tags=["telnyx"])

if _STATIC.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_STATIC),
        name="assets",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _home_page_html() -> str:
    if not _INDEX.is_file():
        logger.error("Missing Hanok landing page: %s (static dir: %s)", _INDEX, _STATIC)
        return (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Setup</title></head>"
            "<body><h1>index.html not found</h1>"
            f"<p>Expected file at: <code>{_INDEX}</code></p>"
            "<p>Redeploy from GitHub so <code>telnyx_restaurant/static/index.html</code> is on the server.</p>"
            "</body></html>"
        )
    return _INDEX.read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
@app.get("/index.html", response_class=HTMLResponse)
def serve_home() -> HTMLResponse:
    """Hanok Table landing page (EN/KO) with Telnyx AI agent widget."""
    return HTMLResponse(content=_home_page_html())
