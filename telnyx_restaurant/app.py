"""FastAPI entrypoint: health, telemetry, Telnyx dynamic webhooks, and static site."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from telnyx_restaurant.routers import webhook

_STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Telnyx Restaurant Reservation API",
    description="Dynamic webhooks and REST backing store for MCP tools (Telnyx AI challenge).",
    version="0.1.0",
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


@app.get("/")
def serve_home() -> FileResponse:
    """Korean restaurant landing page with embedded Telnyx AI agent widget."""
    return FileResponse(_STATIC / "index.html")
