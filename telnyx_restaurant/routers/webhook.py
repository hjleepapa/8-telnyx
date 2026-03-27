"""Telnyx Dynamic Webhook Variables — return JSON for assistant templates.

Map keys to variables configured in Telnyx Portal.

Caller resolution (in order): flat `caller_number` / `from`, then
`data.payload.telnyx_end_user_target` (official assistant.initialization shape).

Lookup matches `guest_phone` using normalized variants (+1 / 11-digit / 10-digit US).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Request
from sqlalchemy import select

from telnyx_restaurant.config import database_url
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.phone_normalize import phone_lookup_variants
from telnyx_restaurant.preorder_calc import preorder_summary_text
from telnyx_restaurant.reminders import build_reminder_speak_text, telnyx_hangup, telnyx_speak
from telnyx_restaurant.webhook_payload import extract_caller_number

router = APIRouter()
logger = logging.getLogger(__name__)

_hanok_cc_lock = threading.Lock()
_hanok_cc_ids: set[str] = set()


def _food_display(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _demo_profile_for_caller(caller_number: str | None) -> dict[str, Any]:
    """Synthetic guests when DB has no row for this ANI."""
    normalized = (caller_number or "").strip()
    if normalized.endswith("0001"):
        return {
            "guest_display_name": "Jordan",
            "vip_tier": "gold",
            "preferred_venue_slug": "harbor-bistro",
            "default_party_size": 4,
            "locale_hint": "en-US",
            "has_upcoming_reservation": True,
            "reservation_preorder_summary": "none",
            "reservation_food_subtotal_cents": 0,
            "reservation_preorder_discount_cents": 0,
            "reservation_food_total_cents": 0,
            "reservation_food_total_display": "$0.00",
            "reservation_has_preorder": False,
            "reservation_source_channel": "demo",
        }
    return {
        "guest_display_name": "Guest",
        "vip_tier": "standard",
        "preferred_venue_slug": "harbor-bistro",
        "default_party_size": 2,
        "locale_hint": "en-US",
        "has_upcoming_reservation": False,
        "reservation_preorder_summary": "none",
        "reservation_food_subtotal_cents": 0,
        "reservation_preorder_discount_cents": 0,
        "reservation_food_total_cents": 0,
        "reservation_food_total_display": "$0.00",
        "reservation_has_preorder": False,
        "reservation_source_channel": "demo",
    }


def _profile_from_db(caller: str | None) -> dict[str, Any] | None:
    if not caller or not database_url():
        return None
    variants = phone_lookup_variants(caller)
    if not variants:
        return None
    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        now = datetime.now(UTC)
        row = db.execute(
            select(Reservation)
            .where(
                Reservation.guest_phone.in_(variants),
                Reservation.starts_at >= now,
                Reservation.status != ReservationStatus.cancelled.value,
            )
            .order_by(Reservation.starts_at.asc())
            .limit(1)
        ).scalar_one_or_none()
        if not row:
            any_row = db.execute(
                select(Reservation)
                .where(Reservation.guest_phone.in_(variants))
                .order_by(Reservation.starts_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if any_row:
                first = any_row.guest_name.split()[0] if any_row.guest_name else "Guest"
                lines_past = any_row.preorder_items
                summary_past = preorder_summary_text(lines_past)
                return {
                    "guest_display_name": first,
                    "vip_tier": "returning",
                    "preferred_venue_slug": "hanok-table",
                    "default_party_size": any_row.party_size,
                    "locale_hint": "en-US",
                    "has_upcoming_reservation": False,
                    "reservation_preorder_summary": summary_past or "none",
                    "reservation_food_subtotal_cents": any_row.food_subtotal_cents,
                    "reservation_preorder_discount_cents": any_row.preorder_discount_cents,
                    "reservation_food_total_cents": any_row.food_total_cents,
                    "reservation_food_total_display": _food_display(any_row.food_total_cents),
                    "reservation_has_preorder": bool(lines_past),
                    "reservation_source_channel": any_row.source_channel,
                }
            return None
        first = row.guest_name.split()[0] if row.guest_name else "Guest"
        lines = row.preorder_items
        summary = preorder_summary_text(lines)
        return {
            "guest_display_name": first,
            "vip_tier": "confirmed_guest",
            "preferred_venue_slug": "hanok-table",
            "default_party_size": row.party_size,
            "locale_hint": "en-US",
            "has_upcoming_reservation": True,
            "next_reservation_code": row.confirmation_code,
            "next_reservation_at": row.starts_at.isoformat(),
            "reservation_preorder_summary": summary or "none",
            "reservation_food_subtotal_cents": row.food_subtotal_cents,
            "reservation_preorder_discount_cents": row.preorder_discount_cents,
            "reservation_food_total_cents": row.food_total_cents,
            "reservation_food_total_display": _food_display(row.food_total_cents),
            "reservation_has_preorder": bool(lines),
            "reservation_source_channel": row.source_channel,
            "demo_reminder_note": (
                "Demo: new bookings schedule an outbound reminder (delay via HANOK_REMINDER_DELAY_SECONDS). "
                "Set TELNYX_API_KEY, TELNYX_CONNECTION_ID, TELNYX_FROM_NUMBER, and point the Call Control app "
                "webhook to POST …/webhooks/telnyx/call-control so the call plays TTS when answered."
            ),
        }
    finally:
        db.close()


def _parse_call_control_event(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    data = body.get("data")
    if isinstance(data, dict):
        et = str(data.get("event_type") or "")
        pl = data.get("payload")
        if isinstance(pl, dict):
            return et, pl
        return et, data
    return str(body.get("event_type") or ""), body


def _extract_call_control_id(payload: dict[str, Any]) -> str | None:
    for key in ("call_control_id", "call_session_id"):
        v = payload.get(key)
        if v:
            return str(v)
    call = payload.get("call")
    if isinstance(call, dict):
        for key in ("call_control_id", "id"):
            v = call.get(key)
            if v:
                return str(v)
    return None


def _decode_client_state(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("client_state")
    if raw is None:
        call = payload.get("call")
        if isinstance(call, dict):
            raw = call.get("client_state")
    if not raw or not isinstance(raw, str):
        return None
    try:
        decoded = base64.b64decode(raw, validate=False)
        obj = json.loads(decoded.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


@router.post("/call-control")
async def telnyx_call_control(request: Request) -> dict[str, str]:
    """Hanok outbound reminder audio: add this URL on the Call Control app used by `TELNYX_CONNECTION_ID`.

    Telnyx sends `call.answered`; we run `speak`, then hang up on `call.speak.ended`.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored"}
    if not isinstance(body, dict):
        return {"status": "ignored"}

    event_type, payload = _parse_call_control_event(body)
    call_control_id = _extract_call_control_id(payload)
    if not call_control_id:
        return {"status": "ok"}

    if event_type == "call.answered":
        state = _decode_client_state(payload)
        if not state or not state.get("hanok_reminder"):
            return {"status": "ok"}
        msg = build_reminder_speak_text(state)
        ok, tag = telnyx_speak(call_control_id, msg)
        if ok:
            with _hanok_cc_lock:
                _hanok_cc_ids.add(call_control_id)
        else:
            logger.warning("Hanok call-control: speak failed tag=%s", tag)
        return {"status": "spoke" if ok else "speak_failed"}

    if event_type in ("call.speak.ended", "call.speak.completed", "call.speak.stopped"):
        do_hangup = False
        with _hanok_cc_lock:
            if call_control_id in _hanok_cc_ids:
                _hanok_cc_ids.discard(call_control_id)
                do_hangup = True
        if do_hangup:
            telnyx_hangup(call_control_id)
            return {"status": "hungup"}
        return {"status": "ok"}

    return {"status": "ok"}


@router.post("/variables")
async def dynamic_webhook_variables(
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Return personalization variables for the AI Assistant instruction templates."""
    data = payload or {}
    caller = extract_caller_number(data)

    db_profile = _profile_from_db(caller)
    profile = db_profile if db_profile else _demo_profile_for_caller(caller)
    profile = {**profile}
    profile["_demo_caller"] = caller or "unknown"
    profile["_source"] = "database" if db_profile else "demo"
    if "demo_reminder_note" not in profile:
        profile["demo_reminder_note"] = (
            "Demo: after a reservation, Hanok schedules an outbound reminder (HANOK_REMINDER_DELAY_SECONDS). "
            "Use the Call Control webhook POST …/webhooks/telnyx/call-control so answered calls speak the reminder."
        )
    return profile
