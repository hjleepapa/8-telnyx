"""Server-rendered admin dashboard for reservations."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from telnyx_restaurant.config import admin_dashboard_token, database_url
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import Reservation
from telnyx_restaurant.preorder_calc import preorder_summary_text

router = APIRouter(tags=["admin"])


def _preorder_summary_short(r: Reservation) -> str:
    text = preorder_summary_text(r.preorder_items)
    return text if text else "—"


def _reservation_calendar_dict(r: Reservation) -> dict:
    """JSON-friendly row for calendar UI (UTC date bucket from starts_at)."""
    lines = r.preorder_items
    return {
        "id": r.id,
        "confirmation_code": r.confirmation_code,
        "guest_name": r.guest_name,
        "guest_phone": r.guest_phone,
        "party_size": r.party_size,
        "starts_at": r.starts_at.isoformat() if r.starts_at else "",
        "starts_at_display": r.starts_at.strftime("%Y-%m-%d %H:%M UTC") if r.starts_at else "",
        "status": r.status,
        "special_requests": r.special_requests,
        "preorder_summary": _preorder_summary_short(r),
        "preorder_items": lines if isinstance(lines, list) else [],
        "food_subtotal_cents": r.food_subtotal_cents,
        "preorder_discount_cents": r.preorder_discount_cents,
        "food_total_cents": r.food_total_cents,
        "source_channel": r.source_channel,
        "reminder_call_status": r.reminder_call_status or "",
    }

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
            request,
            "admin_gate.html",
            {"has_token_param": token is not None},
            status_code=401,
        )

    if not database_url():
        return _TEMPLATES.TemplateResponse(
            request,
            "admin_no_db.html",
            {},
            status_code=503,
        )

    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        return HTMLResponse("Database session unavailable.", status_code=503)

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Reservation).order_by(Reservation.starts_at.asc())
        ).scalars().all()
        cal_rows = [_reservation_calendar_dict(r) for r in rows]
        statuses = sorted({r["status"] for r in cal_rows})
        return _TEMPLATES.TemplateResponse(
            request,
            "admin_reservations.html",
            {
                "reservations_json": json.dumps(cal_rows, ensure_ascii=False),
                "statuses": statuses,
                "row_count": len(cal_rows),
            },
        )
    finally:
        db.close()
