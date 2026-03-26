"""Server-rendered admin dashboard for reservations."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from telnyx_restaurant.config import admin_dashboard_token, database_url
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import Reservation

router = APIRouter(tags=["admin"])

_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


@router.get("/admin/reservations", response_class=HTMLResponse)
def admin_reservations(
    request: Request,
    token: str | None = Query(None),
):
    expected = admin_dashboard_token()
    if expected and token != expected:
        return _TEMPLATES.TemplateResponse(
            "admin_gate.html",
            {
                "request": request,
                "has_token_param": token is not None,
            },
            status_code=401,
        )

    if not database_url():
        return _TEMPLATES.TemplateResponse(
            "admin_no_db.html",
            {"request": request},
            status_code=503,
        )

    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        return HTMLResponse("Database session unavailable.", status_code=503)

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Reservation).order_by(Reservation.starts_at.desc())
        ).scalars().all()
        statuses = sorted({r.status for r in rows})
        return _TEMPLATES.TemplateResponse(
            "admin_reservations.html",
            {
                "request": request,
                "reservations": rows,
                "statuses": statuses,
                "row_count": len(rows),
            },
        )
    finally:
        db.close()
