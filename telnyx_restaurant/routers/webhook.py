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


def _decode_client_state_blob(raw: str | None) -> dict[str, Any] | None:
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    try:
        decoded = base64.b64decode(s, validate=False)
        obj = json.loads(decoded.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _decode_client_state(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("client_state", "clientState"):
        got = _decode_client_state_blob(payload.get(key) if isinstance(payload.get(key), str) else None)
        if got:
            return got
    call = payload.get("call")
    if isinstance(call, dict):
        for key in ("client_state", "clientState"):
            got = _decode_client_state_blob(call.get(key) if isinstance(call.get(key), str) else None)
            if got:
                return got
    return None


def _walk_for_client_state(obj: Any, depth: int = 0) -> dict[str, Any] | None:
    """Telnyx occasionally nests `client_state` outside the slice we first parse."""
    if depth > 8 or not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if k in ("client_state", "clientState") and isinstance(v, str):
            got = _decode_client_state_blob(v)
            if got and got.get("hanok_reminder"):
                return got
        if isinstance(v, dict):
            got = _walk_for_client_state(v, depth + 1)
            if got:
                return got
    return None


def _callee_number(payload: dict[str, Any]) -> str | None:
    for key in ("to", "callee", "called_number", "destination_number"):
        v = payload.get(key)
        if v:
            return str(v).strip()
    call = payload.get("call")
    if isinstance(call, dict):
        for key in ("to", "callee", "destination"):
            v = call.get(key)
            if v:
                return str(v).strip()
    return None


def _reminder_state_from_db_for_phone(callee: str) -> dict[str, Any] | None:
    """If `client_state` is missing from webhooks, rebuild speak text from the latest reservation row."""
    if not callee or not database_url():
        return None
    variants = phone_lookup_variants(callee)
    if not variants:
        return None
    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        row = db.execute(
            select(Reservation)
            .where(
                Reservation.guest_phone.in_(variants),
                Reservation.status != ReservationStatus.cancelled.value,
            )
            .order_by(Reservation.starts_at.desc())
        ).scalar_one_or_none()
        if not row:
            return None
        first = (row.guest_name or "Guest").split()[0]
        dt = row.starts_at
        when = dt.strftime("%A, %B %d at %I:%M %p %Z") if dt.tzinfo else dt.strftime("%A, %B %d at %I:%M %p")
        summ = preorder_summary_text(row.preorder_items)
        if len(summ) > 480:
            summ = summ[:477].rsplit(";", 1)[0] + ", and more."
        return {
            "hanok_reminder": True,
            "confirmation_code": row.confirmation_code,
            "guest_first_name": first,
            "guest_full_name": (row.guest_name or "").strip(),
            "party_size": row.party_size,
            "starts_at_speech": when,
            "preorder_summary": summ,
        }
    finally:
        db.close()


def _normalize_call_control_event_type(event_type: str) -> str:
    et = (event_type or "").strip().lower().replace("-", ".")
    if et == "callanswered" or et.endswith(".answered"):
        return "call.answered"
    if "speak.ended" in et or "speak.completed" in et or "speak.stopped" in et:
        return "call.speak.ended"
    return et


def _resolve_reminder_state(body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    """Recover speak payload from client_state, webhook shape quirks, or DB by callee number."""
    state = _decode_client_state(payload) or _walk_for_client_state(body)
    if state and state.get("hanok_reminder"):
        return state
    callee = _callee_number(payload)
    if callee:
        fallback = _reminder_state_from_db_for_phone(callee)
        if fallback:
            logger.info("Hanok call-control: reminder state from DB for %s", callee)
            return fallback
    if state:
        logger.warning(
            "Hanok call-control: decoded client_state without hanok_reminder flag; keys=%s",
            list(state.keys())[:12],
        )
    else:
        logger.warning(
            "Hanok call-control: no client_state and no DB row for callee=%r payload_keys=%s",
            callee,
            list(payload.keys())[:20],
        )
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
    event_type_raw = event_type
    event_type = _normalize_call_control_event_type(event_type)
    logger.info(
        "hanok_call_control event_raw=%r event_norm=%r top=%s payload_keys=%s",
        event_type_raw,
        event_type,
        list(body.keys())[:12],
        list(payload.keys())[:24] if isinstance(payload, dict) else None,
    )
    call_control_id = _extract_call_control_id(payload)
    if not call_control_id:
        return {"status": "ok"}

    if event_type == "call.answered":
        state = _resolve_reminder_state(body, payload)
        if not state:
            return {"status": "no_state"}
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
