"""REST API for reservations (MCP / voice tools will call this)."""

from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from telnyx_restaurant.db import get_db
from telnyx_restaurant.menu_catalog import MENU_ITEMS
from telnyx_restaurant.phone_normalize import phone_lookup_variants
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
                "In Telnyx, define a real Path/query parameter (e.g. confirmation_code) on the tool—"
                "do not type {{code}} in the URL. To find a booking without the code, use "
                "GET /api/reservations/lookup-by-phone?phone={{caller_number}} "
                "(map phone to telnyx_end_user_target from dynamic variables)."
            ),
        )
    return v


def _gen_confirmation_code() -> str:
    part = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"HNK-{part}"


def _guest_name_matches(stored_full: str, hint: str) -> bool:
    """Case-insensitive match: full substring, first-name, or shared tokens."""
    s = (stored_full or "").strip().casefold()
    h = hint.strip().casefold()
    if not h:
        return False
    if not s:
        return False
    if h == s or h in s or s in h:
        return True
    s_parts = s.split()
    h_parts = h.split()
    if h_parts and s_parts and h_parts[0] == s_parts[0]:
        return True
    return bool(set(h_parts) & set(s_parts))


def _candidate_pool_for_phone(
    db: Session, variants: list[str], now: datetime
) -> list[Reservation]:
    rows = list(
        db.execute(
            select(Reservation)
            .where(
                Reservation.guest_phone.in_(variants),
                Reservation.status != ReservationStatus.cancelled.value,
            )
            .order_by(Reservation.starts_at.desc())
        )
        .scalars()
        .all()
    )
    upcoming = [r for r in rows if r.starts_at >= now]
    if upcoming:
        return sorted(upcoming, key=lambda r: r.starts_at)
    return sorted(rows, key=lambda r: r.starts_at, reverse=True)


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


@router.get("/lookup-by-phone", response_model=ReservationRead)
def lookup_reservation_by_guest_phone(
    phone: str = Query(
        ...,
        min_length=3,
        description="Guest phone as collected on the call or from dynamic variables (any common US format).",
    ),
    guest_name: str | None = Query(
        None,
        min_length=1,
        max_length=255,
        description="Required when more than one active reservation shares this phone (disambiguate).",
    ),
    db: Session = Depends(get_db),
):
    """Find a reservation by `guest_phone` (voice assistant). Does not require confirmation code.

    Prefers upcoming non-cancelled rows (nearest first), else most recent. If several rows share the
    phone, pass `guest_name` (spoken or on file) so we can pick the right one.
    Map Telnyx `telnyx_end_user_target` into query param `phone` from the tool.
    """
    if "{{" in phone or "}}" in phone:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsubstituted template in phone query. Use a tool query/path parameter bound to the "
                "caller number (e.g. telnyx_end_user_target), not literal {{…}} in the URL."
            ),
        )
    if guest_name and ("{{" in guest_name or "}}" in guest_name):
        raise HTTPException(
            status_code=400,
            detail="Unsubstituted template in guest_name query.",
        )
    variants = phone_lookup_variants(phone.strip())
    if not variants:
        raise HTTPException(status_code=400, detail="Invalid or empty phone value.")

    now = datetime.now(UTC)
    pool = _candidate_pool_for_phone(db, variants, now)
    if not pool:
        raise HTTPException(
            status_code=404,
            detail="No reservation found for this phone number.",
        )

    if len(pool) == 1:
        return pool[0]

    # Multiple rows on this line: need a name match unless caller already unique by schedule
    hint = (guest_name or "").strip()
    if not hint:
        raise HTTPException(
            status_code=400,
            detail=(
                "Multiple reservations share this phone number. Ask which name the booking is under, "
                "then call again with query parameter guest_name (e.g. first name or full name as on the reservation)."
            ),
        )

    matched = [r for r in pool if _guest_name_matches(r.guest_name, hint)]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise HTTPException(
            status_code=404,
            detail="No reservation on this phone matches that guest name.",
        )
    raise HTTPException(
        status_code=409,
        detail="Multiple reservations still match this phone and name; ask for the confirmation code (HNK-…).",
    )


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
