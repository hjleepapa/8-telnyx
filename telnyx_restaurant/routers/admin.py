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
from telnyx_restaurant.preorder_calc import preorder_summary_text

router = APIRouter(tags=["admin"])


def _preorder_summary_short(r: Reservation) -> str:
    text = preorder_summary_text(r.preorder_items)
    return text if text else "—"

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
            select(Reservation).order_by(Reservation.starts_at.desc())
        ).scalars().all()
        # Plain dicts: TemplateResponse renders after this function returns and the
        # session is closed; ORM instances would detach and break Jinja access.
        reservations = [
            {
                "confirmation_code": r.confirmation_code,
                "guest_name": r.guest_name,
                "guest_phone": r.guest_phone,
                "party_size": r.party_size,
                "starts_at": r.starts_at,
                "status": r.status,
                "special_requests": r.special_requests,
                "preorder_summary": _preorder_summary_short(r),
                "food_subtotal_cents": r.food_subtotal_cents,
                "preorder_discount_cents": r.preorder_discount_cents,
                "food_total_cents": r.food_total_cents,
                "source_channel": r.source_channel,
                "reminder_call_status": r.reminder_call_status,
            }
            for r in rows
        ]
        statuses = sorted({r["status"] for r in reservations})
        return _TEMPLATES.TemplateResponse(
            request,
            "admin_reservations.html",
            {
                "reservations": reservations,
                "statuses": statuses,
                "row_count": len(reservations),
            },
        )
    finally:
        db.close()
