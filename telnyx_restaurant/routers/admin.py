"""Server-rendered admin dashboard for reservations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from telnyx_restaurant.config import admin_dashboard_token, database_url, hanok_vip_preorder_threshold_cents
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.preorder_calc import preorder_summary_text

router = APIRouter(tags=["admin"])


def _display_tz() -> ZoneInfo:
    return ZoneInfo((os.environ.get("HANOK_ADMIN_DISPLAY_TIMEZONE") or "America/Los_Angeles").strip())


def _starts_at_display_local(r: Reservation) -> str:
    """Human-readable wall time for admin UI (restaurant-local TZ; stored UTC)."""
    if not r.starts_at:
        return ""
    wall = r.starts_at.astimezone(_display_tz())
    return (
        wall.strftime("%Y-%m-%d %I:%M %p ")
        + wall.tzname()
        + " (your time)"
    )


def _preorder_summary_short(r: Reservation) -> str:
    text = preorder_summary_text(r.preorder_items)
    return text if text else "—"


def _reservation_calendar_dict(r: Reservation) -> dict:
    """JSON-friendly row for calendar UI (calendar groups by HANOK_ADMIN_DISPLAY_TIMEZONE day)."""
    lines = r.preorder_items
    tabs = r.tables_allocated
    return {
        "id": r.id,
        "confirmation_code": r.confirmation_code,
        "guest_name": r.guest_name,
        "guest_phone": r.guest_phone,
        "party_size": r.party_size,
        "starts_at": r.starts_at.isoformat() if r.starts_at else "",
        "starts_at_display": _starts_at_display_local(r),
        "status": r.status,
        "special_requests": r.special_requests,
        "preorder_summary": _preorder_summary_short(r),
        "preorder_items": lines if isinstance(lines, list) else [],
        "food_subtotal_cents": r.food_subtotal_cents,
        "preorder_discount_cents": r.preorder_discount_cents,
        "food_total_cents": r.food_total_cents,
        "source_channel": r.source_channel,
        "preferred_locale": getattr(r, "preferred_locale", None) or "en",
        "reminder_call_status": r.reminder_call_status or "",
        "seating_status": getattr(r, "seating_status", None) or "not_applicable",
        "guest_priority": getattr(r, "guest_priority", None) or "normal",
        "tables_allocated": tabs if tabs else [],
        "duration_minutes": int(getattr(r, "duration_minutes", None) or 120),
        "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else "",
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
        rows = list(
            db.execute(select(Reservation).order_by(Reservation.starts_at.asc())).scalars().all()
        )
        cancelled = ReservationStatus.cancelled.value
        active = [r for r in rows if r.status != cancelled]
        cal_rows = [_reservation_calendar_dict(r) for r in active]
        statuses = sorted({r["status"] for r in cal_rows}) if cal_rows else []
        cancelled_n = sum(1 for r in rows if r.status == cancelled)
        display_tz = (os.environ.get("HANOK_ADMIN_DISPLAY_TIMEZONE") or "America/Los_Angeles").strip()
        return _TEMPLATES.TemplateResponse(
            request,
            "admin_reservations.html",
            {
                "reservations_json": json.dumps(cal_rows, ensure_ascii=False),
                "statuses": statuses,
                "row_count": len(cal_rows),
                "cancelled_hidden": cancelled_n,
                "calendar_display_tz": display_tz,
                "calendar_tz_label": "Pacific (PT)",
                "vip_preorder_threshold_cents": hanok_vip_preorder_threshold_cents(),
            },
        )
    finally:
        db.close()
