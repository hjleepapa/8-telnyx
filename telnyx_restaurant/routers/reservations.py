"""REST API for reservations (MCP / voice tools will call this)."""

from __future__ import annotations

import json
import re
import secrets
import string
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import Request

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
    ReservationUpdate,
)

router = APIRouter(prefix="/api/reservations", tags=["reservations"])


def _reject_modifying_cancelled(row: Reservation) -> None:
    if row.status == ReservationStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail="Reservation is cancelled; create a new booking or change status first.",
        )


def _apply_reservation_update(row: Reservation, body: ReservationUpdate) -> None:
    if not body.model_fields_set:
        return
    if "guest_name" in body.model_fields_set:
        row.guest_name = body.guest_name  # type: ignore[assignment]
    if "guest_phone" in body.model_fields_set:
        row.guest_phone = body.guest_phone.strip()  # type: ignore[union-attr]
    if "party_size" in body.model_fields_set:
        row.party_size = body.party_size  # type: ignore[assignment]
    if "starts_at" in body.model_fields_set:
        st = body.starts_at
        if st is not None:
            row.starts_at = st if st.tzinfo else st.replace(tzinfo=UTC)
    if "special_requests" in body.model_fields_set:
        row.special_requests = body.special_requests
    if "preorder" in body.model_fields_set:
        if body.preorder is None:
            row.preorder_json = None
            row.food_subtotal_cents = 0
            row.preorder_discount_cents = 0
            row.food_total_cents = 0
        else:
            try:
                preorder_json, subtotal, discount, total = serialize_preorder(body.preorder)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            row.preorder_json = preorder_json
            row.food_subtotal_cents = subtotal
            row.preorder_discount_cents = discount
            row.food_total_cents = total
    row.updated_at = datetime.now(UTC)


def _parse_status_update(
    body: dict[str, Any],
    status: str | None = None,
) -> ReservationStatusUpdate:
    """Telnyx tools often send `{}`, nested keys, or only a query param."""
    merged = dict(body or {})
    if (status or "").strip():
        merged.setdefault("status", status.strip())
    try:
        return ReservationStatusUpdate.model_validate(merged)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "Send JSON {\"status\":\"cancelled\"} (or cancel/canceled), nested {data:{...}}, "
                "or query ?status=cancelled. "
                f"Pydantic: {exc.errors()}"
            ),
        ) from exc


async def read_status_request_payload(request: Request) -> dict[str, Any]:
    """Telnyx HTTP tools often use form encoding or plain text, not JSON — avoid FastAPI Body 422."""
    hdr = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in hdr or "multipart/form-data" in hdr:
        try:
            form = await request.form()
        except Exception:
            return {}
        out: dict[str, Any] = {}
        for key, val in form.multi_items():
            if hasattr(val, "read"):
                continue
            out[str(key)] = str(val)
        return out

    raw = await request.body()
    if not raw or not raw.strip():
        return {}

    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if "json" in ct or ct in ("", "text/plain", "application/json", "text/json", "application/problem+json"):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace").strip()
            return {"status": text} if text else {}
        if isinstance(data, dict):
            return data
        if isinstance(data, str):
            return {"status": data}
        return {}

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace").strip()
        return {"status": text} if text else {}
    return data if isinstance(data, dict) else {"status": str(data)}


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


def _normalize_confirmation_code(code: str) -> str:
    """Fix ASR/LLM dropping the hyphen or appending junk: HNKWGJF → HNK-WGJF; HNK-WJGK-F → HNK-WJGK."""
    c = (code or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"HNK-[A-Z0-9]{4}", c):
        return c
    m = re.fullmatch(r"HNK([A-Z0-9]{4})", c)
    if m:
        return f"HNK-{m.group(1)}"
    m2 = re.match(r"^(HNK-[A-Z0-9]{4})", c)
    if m2:
        return m2.group(1)
    return c


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
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/by-code/{code}/status", response_model=ReservationRead)
async def update_status_by_code(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = Query(
        None,
        description="Optional when JSON body is empty or omits status (Telnyx query-only tools).",
    ),
):
    """Update status using HNK-… code (single Telnyx webhook; no numeric id)."""
    body = await read_status_request_payload(request)
    parsed = _parse_status_update(body, status)
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    row.status = parsed.status
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/by-code/{code}", response_model=ReservationRead)
def patch_reservation_by_code(
    code: str,
    body: ReservationUpdate,
    db: Session = Depends(get_db),
):
    """Update party size, time, pre-order, or guest fields using confirmation code (voice-friendly)."""
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    _reject_modifying_cancelled(row)
    if not body.model_fields_set:
        return row
    _apply_reservation_update(row, body)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{reservation_id}", response_model=ReservationRead)
def get_reservation(reservation_id: int, db: Session = Depends(get_db)):
    row = db.get(Reservation, reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/{reservation_id}", response_model=ReservationRead)
def patch_reservation(
    reservation_id: int,
    body: ReservationUpdate,
    db: Session = Depends(get_db),
):
    """Update party size, time, pre-order, or guest details (partial PATCH)."""
    row = db.get(Reservation, reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    _reject_modifying_cancelled(row)
    if not body.model_fields_set:
        return row
    _apply_reservation_update(row, body)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{reservation_id}/status", response_model=ReservationRead)
async def update_status(
    reservation_id: int,
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = Query(
        None,
        description="Optional when JSON body is empty or omits status (Telnyx query-only tools).",
    ),
):
    body = await read_status_request_payload(request)
    parsed = _parse_status_update(body, status)
    row = db.get(Reservation, reservation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    row.status = parsed.status
    row.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return row
