"""REST API for reservations (MCP / voice tools will call this)."""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from telnyx_restaurant.db import get_db
from telnyx_restaurant.menu_catalog import MENU_ITEMS
from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.preorder_calc import serialize_preorder
from telnyx_restaurant.reminders import schedule_demo_reminder_call
from telnyx_restaurant.schemas_res import (
    ReservationCreate,
    ReservationRead,
    ReservationStatusUpdate,
)

router = APIRouter(prefix="/api/reservations", tags=["reservations"])


def _reject_unsubstituted_path_value(value: str, *, field: str = "code") -> str:
    """Telnyx/webhook misconfig often leaves {{code}} in the path; fail loudly."""
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail=f"Missing {field}.")
    if "{{" in v or "}}" in v:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsubstituted URL template in {field}: {v!r}. "
                "Use the tool Path parameter (e.g. confirmation_code) so Telnyx "
                "fills the value—not literal {{code}} in the webhook URL."
            ),
        )
    return v


def _gen_confirmation_code() -> str:
    part = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"HNK-{part}"


@router.get("/menu/items")
def list_menu_items():
    """Public menu with prices for the online reservation pre-order step."""
    return [m.as_public() for m in MENU_ITEMS]


@router.get("", response_model=list[ReservationRead])
def list_reservations(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(Reservation).order_by(Reservation.starts_at.desc())
    if status:
        q = q.where(Reservation.status == status)
    return list(db.execute(q).scalars().all())


@router.post("", response_model=ReservationRead)
def create_reservation(
    body: ReservationCreate,
    db: Session = Depends(get_db),
):
    code = _gen_confirmation_code()
    for _ in range(10):
        if not db.execute(
            select(Reservation.id).where(Reservation.confirmation_code == code)
        ).first():
            break
        code = _gen_confirmation_code()

    try:
        preorder_json, subtotal, discount, total = serialize_preorder(body.preorder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    row = Reservation(
        confirmation_code=code,
        guest_name=body.guest_name,
        guest_phone=body.guest_phone.strip(),
        party_size=body.party_size,
        starts_at=(
            body.starts_at
            if body.starts_at.tzinfo
            else body.starts_at.replace(tzinfo=UTC)
        ),
        status=ReservationStatus.confirmed.value,
        special_requests=body.special_requests,
        preorder_json=preorder_json,
        food_subtotal_cents=subtotal,
        preorder_discount_cents=discount,
        food_total_cents=total,
        source_channel=body.source_channel,
        reminder_call_status="reminder_queued",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    schedule_demo_reminder_call(
        reservation_id=row.id,
        guest_phone=row.guest_phone,
        guest_name=row.guest_name,
        confirmation_code=row.confirmation_code,
    )
    return row


@router.get("/by-code/{code}", response_model=ReservationRead)
def get_reservation_by_code(code: str, db: Session = Depends(get_db)):
    code = _reject_unsubstituted_path_value(code)
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/by-code/{code}/status", response_model=ReservationRead)
def update_status_by_code(
    code: str,
    body: ReservationStatusUpdate,
    db: Session = Depends(get_db),
):
    """Update status using HNK-… code (single Telnyx webhook; no numeric id)."""
    code = _reject_unsubstituted_path_value(code)
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    row.status = body.status
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{reservation_id}", response_model=ReservationRead)
def get_reservation(reservation_id: int, db: Session = Depends(get_db)):
    row = db.get(Reservation, reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/{reservation_id}/status", response_model=ReservationRead)
def update_status(
    reservation_id: int,
    body: ReservationStatusUpdate,
    db: Session = Depends(get_db),
):
    row = db.get(Reservation, reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    row.status = body.status
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row
