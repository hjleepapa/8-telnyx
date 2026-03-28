"""FastAPI entrypoint: health, telemetry, Telnyx dynamic webhooks, and static site."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from telnyx_restaurant.config import (
    admin_dashboard_token,
    hanok_public_base_url,
    hanok_reservation_lab_enabled,
    telnyx_api_key,
    telnyx_connection_id,
)
from telnyx_restaurant.routers import admin, reservations, webhook

logger = logging.getLogger(__name__)

_STATIC = Path(__file__).resolve().parent / "static"
_INDEX = _STATIC / "index.html"
_RESERVE_ONLINE = _STATIC / "reserve_online.html"
_RESERVATION_STATUS = _STATIC / "reservation_status.html"
_RESERVATION_LAB = _STATIC / "reservation_lab.html"
_APP_REV = os.environ.get("RENDER_GIT_COMMIT", os.environ.get("APP_GIT_REVISION", "local"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Log deploy fingerprint; create tables and optional demo seed."""
    log = logging.getLogger("uvicorn.error")
    log.warning(
        "Hanok Table: rev=%s index_exists=%s app_py=%s",
        _APP_REV[:12] if _APP_REV else "?",
        _INDEX.is_file(),
        Path(__file__).resolve(),
    )
    if telnyx_api_key() and telnyx_connection_id() and not hanok_public_base_url():
        log.warning(
            "Set HANOK_PUBLIC_BASE_URL (https://your-host) so reminder dials send Call Control webhooks "
            "to /webhooks/telnyx/call-control; otherwise answered calls may stay silent."
        )
    try:
        from telnyx_restaurant.db import SessionLocal, init_db
        from telnyx_restaurant.seed import seed_demo_reservations

        if init_db() and SessionLocal is not None:
            db = SessionLocal()
            try:
                n = seed_demo_reservations(db)
                if n:
                    log.warning("Seeded %s demo reservations (empty table)", n)
            except Exception:
                log.exception("Demo seed failed — check DB_URI and DB permissions")
            finally:
                db.close()
    except Exception:
        log.exception("Database startup skipped — app will run without Postgres until DB is reachable")
    yield


app = FastAPI(
    title="Telnyx Restaurant Reservation API",
    description="Dynamic webhooks and REST backing store for MCP tools (Telnyx AI challenge).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(webhook.router, prefix="/webhooks/telnyx", tags=["telnyx"])
app.include_router(reservations.router)
app.include_router(admin.router)

if _STATIC.is_dir():
    app.mount(
        "/assets",
        StaticFiles(directory=_STATIC),
        name="assets",
    )


@app.get("/health")
@app.post("/health")
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


def _read_static_html(path: Path, label: str) -> HTMLResponse:
    if not path.is_file():
        logger.error("Missing static HTML: %s (%s)", path, label)
        return HTMLResponse(
            f"<!DOCTYPE html><html><body><h1>Missing {label}</h1><p>Expected <code>{path}</code></p></body></html>",
            status_code=404,
        )
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.get("/reserve-online", response_class=HTMLResponse)
@app.get("/reserve-online.html", response_class=HTMLResponse)
def serve_reserve_online() -> HTMLResponse:
    """Pre-order form, 7% food discount, posts to REST API."""
    return _read_static_html(_RESERVE_ONLINE, "reserve_online.html")


@app.get("/reservation/status", response_class=HTMLResponse)
@app.get("/reservation-status.html", response_class=HTMLResponse)
def serve_reservation_status() -> HTMLResponse:
    """Guest-facing status & food totals (confirmation code)."""
    return _read_static_html(_RESERVATION_STATUS, "reservation_status.html")


@app.get("/reservation-lab", response_class=HTMLResponse)
@app.get("/reservation-lab.html", response_class=HTMLResponse)
def serve_reservation_lab(
    token: str | None = Query(None, description="Must match ADMIN_DASHBOARD_TOKEN when that env is set."),
) -> HTMLResponse:
    """Optional browser UI: create / lookup / amend / canned scenarios (dev & demos). Off unless HANOK_RESERVATION_LAB=1."""
    if not hanok_reservation_lab_enabled():
        return HTMLResponse("Not found.", status_code=404)
    expected = admin_dashboard_token()
    if expected and token != expected:
        return HTMLResponse(
            (
                "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Lab</title></head><body>"
                "<p>Unauthorized. Open <code>/reservation-lab?token=…</code> with "
                "<code>ADMIN_DASHBOARD_TOKEN</code>.</p><p><a href='/'>Home</a></p></body></html>"
            ),
            status_code=401,
        )
    return _read_static_html(_RESERVATION_LAB, "reservation_lab.html")
