"""Telnyx Dynamic Webhook Variables — return JSON for assistant templates.

Map keys to variables configured in Telnyx Portal.

Caller resolution gathers `telnyx_end_user_target`, `caller_number`, and `from` (flat and
`data.payload`, nested first) and returns the first value that looks like a PSTN / E.164
token. Opaque SIP identities such as ``user@sip.telnyx.com`` (without a ``sip:+1555…`` user
part) are skipped so a real number field still wins—otherwise premium pre-order variables
would fall back to the zero-food demo profile.

Lookup matches `guest_phone` using normalized variants (+1 / 11-digit / 10-digit US).

High-value pre-orders (``food_total_cents`` ≥ ``HANOK_PREMIUM_PREORDER_CENTS``, default 30000 = $300)
add ``guest_is_high_value_preorder``, ``guest_preorder_value_tier``, ``concierge_service_hint``,
and ``cancel_retention_offer`` (complimentary-meal / credit language for cancel intent).

With ``HANOK_TABLE_ALLOCATION_ENABLED``, reservations expose ``reservation_seating_status``,
``guest_waitlist_priority``, ``waitlist_fairness_hint``, and wait-time fields
(``guest_waitlist_position``, ``guest_waitlist_queue_size``, ``guest_waitlist_estimated_wait_minutes``,
``guest_waitlist_position_ordinal_en``, ``guest_waitlist_wait_time_hint``, ``guest_waitlist_max_parties_per_slot``,
``guest_waitlist_alternate_time_hint``) aligned with promotion order
(position × ``HANOK_WAITLIST_MINUTES_PER_POSITION``, default 15 minutes). Waitlist volume is capped by
a **weighted** sum: ``HANOK_WAITLIST_MAX_PER_SLOT`` (default 5) limits total units where multi-table parties
count extra; additional waitlist joins receive HTTP 409. ``guest_waitlist_max_parties_per_slot`` echoes that cap.

``preferred_locale`` on the guest's reservation (``en`` / ``ko``) sets ``locale_hint`` (``en-US`` / ``ko-KR``)
for Telnyx instructions, e.g. “Conduct the conversation in Korean when ``locale_hint`` is ``ko-KR``.”
Online booking in Korean sends ``preferred_locale: ko``; the PSTN call does not read browser localStorage alone.

**Caller ID (shared demo line):** responses may include ``caller_phone_telnyx``, ``caller_phone_normalized``,
``caller_line_reservation_count``, ``caller_line_single_booking`` (yes/no), ``caller_line_has_multiple_bookings``
(yes/no), ``guest_personalized_greeting_suggestion`` (when a single booking matches the line),
``caller_line_booking_guest_names_hint``, and ``guest_lookup_identification_hint``. The assistant should greet
by name **without re-asking** when ``caller_line_single_booking`` is yes, and ask for name only when multiple
upcoming bookings share the number.
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

from telnyx_restaurant.config import (
    database_url,
    hanok_premium_preorder_cents_threshold,
    hanok_table_allocation_enabled,
    hanok_vip_preorder_threshold_cents,
    hanok_waitlist_max_per_slot,
    hanok_waitlist_minutes_per_position,
)
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.phone_normalize import phone_lookup_variants, to_e164_us
from telnyx_restaurant.preorder_calc import preorder_summary_text
from telnyx_restaurant.reminders import build_reminder_speak_text, telnyx_hangup, telnyx_speak
from telnyx_restaurant.seating_service import waitlist_queue_metadata
from telnyx_restaurant.locale_prefs import assistant_locale_hint
from telnyx_restaurant.webhook_payload import extract_caller_number
from telnyx_restaurant.routers.reservations import reservation_candidates_for_caller_line

router = APIRouter()
logger = logging.getLogger(__name__)

_hanok_cc_lock = threading.Lock()
_hanok_cc_ids: set[str] = set()


def _locale_profile_fields(row: Reservation) -> dict[str, Any]:
    pref = (getattr(row, "preferred_locale", None) or "en").strip() or "en"
    return {"preferred_locale": pref, "locale_hint": assistant_locale_hint(pref)}


def _food_display(cents: int) -> str:
    return f"${cents / 100:.2f}"


def _premium_concierge_variables(*, food_total_cents: int, threshold: int | None = None) -> dict[str, Any]:
    """Telnyx instruction templates: high-value pre-order guests get VIP-style hints for cancel/change flows."""
    th = hanok_premium_preorder_cents_threshold() if threshold is None else threshold
    is_premium = food_total_cents >= th and th > 0
    tier = "premium_preorder" if is_premium else "standard"
    hint = (
        "High-value pre-order guest: acknowledge their spend. Offer proactive help—complimentary banchan, "
        "a small chef-selected dessert, or priority rebooking if they need to change time or party size. "
        "If they cancel, thank them warmly and invite them back."
        if is_premium
        else (
            "Standard guest: be clear and helpful on changes or cancellation; offer menu or pre-order help if relevant."
        )
    )
    retention = (
        "If they ask to cancel, briefly offer complimentary chef-selected banchan and a dessert on us, "
        "or a future reservation credit—only with manager confirmation if your policy requires it."
        if is_premium
        else "No automatic retention offer; follow standard cancellation policy."
    )
    return {
        "guest_is_high_value_preorder": "yes" if is_premium else "no",
        "guest_preorder_value_tier": tier,
        "guest_preorder_total_crossed_premium_threshold": "yes" if is_premium else "no",
        "concierge_service_hint": hint,
        "cancel_retention_offer": retention,
    }


def _seating_waitlist_profile(
    *,
    food_total_cents: int,
    guest_priority_raw: str | None,
    seating_status_raw: str | None,
) -> dict[str, Any]:
    """Waitlist / VIP ordering context for Telnyx templates (requires HANOK_TABLE_ALLOCATION_ENABLED)."""
    if not hanok_table_allocation_enabled():
        return {
            "reservation_seating_status": "not_applicable",
            "guest_waitlist_priority": "normal",
            "waitlist_fairness_hint": (
                "Table allocation is disabled for this deployment; waitlist promotion rules do not apply."
            ),
        }
    status = (seating_status_raw or "not_applicable").strip() or "not_applicable"
    priority = (guest_priority_raw or "normal").strip().lower()
    if priority not in ("vip", "normal"):
        priority = "normal"
    vip_threshold = hanok_vip_preorder_threshold_cents()
    is_vip = priority == "vip" or int(food_total_cents) >= vip_threshold

    if status == "waitlist":
        if is_vip:
            hint = (
                "Guest is waitlisted with VIP priority: when a table opens because another reservation cancelled, "
                "this guest is confirmed before standard-priority waitlisted guests, even if they joined later."
            )
        else:
            hint = (
                "Guest is on the waitlist with standard priority: VIP guests (or guests with a very large pre-order) "
                "are offered the next open table first."
            )
    elif status == "allocated":
        hint = "Table is allocated for this reservation; not waitlisted."
    elif status == "not_applicable":
        hint = "No waitlist state for this booking (table allocation not used or slot does not require waitlist)."
    else:
        hint = f"Seating status: {status}."

    out_sw: dict[str, Any] = {
        "reservation_seating_status": status,
        "guest_waitlist_priority": "vip" if is_vip else "normal",
        "waitlist_fairness_hint": hint,
    }
    if hanok_table_allocation_enabled():
        out_sw["guest_waitlist_max_parties_per_slot"] = str(hanok_waitlist_max_per_slot())
        out_sw["guest_waitlist_alternate_time_hint"] = (
            "If this seating time cannot be booked or the waitlist is full, offer the same party size "
            "about two hours earlier or about two hours later."
        )
    return out_sw


_WAITLIST_ORDINALS_EN = (
    "",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
)


def _waitlist_position_ordinal_en(n: int) -> str:
    if 1 <= n <= 10:
        return _WAITLIST_ORDINALS_EN[n]
    return f"number {n}"


def _waitlist_queue_speech_variables(
    *,
    queue_meta: dict[str, Any] | None,
    seating_status_resolved: str | None,
) -> dict[str, str]:
    """Telnyx templates: queue slot, ETA minutes (position × per-slot minutes), and a speakable hint."""
    if not hanok_table_allocation_enabled():
        return {
            "guest_waitlist_position": "n/a",
            "guest_waitlist_queue_size": "n/a",
            "guest_waitlist_estimated_wait_minutes": "n/a",
            "guest_waitlist_position_ordinal_en": "n/a",
            "guest_waitlist_tables_required": "n/a",
            "guest_waitlist_can_seat_after_ahead": "n/a",
            "guest_waitlist_ahead_queue_feasible": "n/a",
            "guest_waitlist_seating_capacity_hint": "Table allocation is disabled; waitlist position does not apply.",
            "guest_waitlist_wait_time_hint": "Table allocation is disabled; waitlist position does not apply.",
        }
    status = (seating_status_resolved or "not_applicable").strip() or "not_applicable"
    if status != "waitlist" or queue_meta is None:
        empty = {
            "guest_waitlist_position": "0",
            "guest_waitlist_queue_size": "0",
            "guest_waitlist_estimated_wait_minutes": "0",
            "guest_waitlist_position_ordinal_en": "",
            "guest_waitlist_tables_required": "0",
            "guest_waitlist_can_seat_after_ahead": "yes",
            "guest_waitlist_ahead_queue_feasible": "yes",
            "guest_waitlist_seating_capacity_hint": (
                "The guest is not on a table waitlist for this reservation (seating is allocated or not applicable)."
            ),
            "guest_waitlist_wait_time_hint": (
                "The guest is not on a table waitlist for this reservation (seating is allocated or not applicable)."
            ),
        }
        return empty
    pos = int(queue_meta["position"])
    n = int(queue_meta["queue_size"])
    est = int(queue_meta["estimated_wait_minutes"])
    ordinal = _waitlist_position_ordinal_en(pos)
    tables_req = int(queue_meta.get("tables_required", 0))
    feas = bool(queue_meta.get("feasible_after_ahead", True))
    ahead_ok = bool(queue_meta.get("ahead_chain_ok", True))

    if not feas:
        cap_tail = (
            "Given table inventory and the parties ahead of you in line, we may not have enough tables together "
            "at this seating time for your party size—especially if your group needs more than one table. "
            "Do not promise a table at this time. Suggest checking availability about two hours earlier or "
            "about two hours later, or another time the guest prefers."
        )
        seating_cap = cap_tail
    elif tables_req >= 2:
        cap_tail = (
            f"Your party is planned across about {tables_req} tables for seating purposes. "
            "Tables are still waitlist-only until one is assigned."
        )
        seating_cap = cap_tail
    else:
        cap_tail = ""
        seating_cap = (
            "Standard waitlist: position and ETA apply; this party should fit after earlier waitlist parties "
            "given current table counts."
        )

    wait_parts = [
        f"You are {ordinal} in line for this seating time ({pos} of {n} on the waitlist).",
        f"Estimated wait is about {est} minutes.",
    ]
    if cap_tail:
        wait_parts.append(cap_tail)
    wait_hint = " ".join(wait_parts)

    return {
        "guest_waitlist_position": str(pos),
        "guest_waitlist_queue_size": str(n),
        "guest_waitlist_estimated_wait_minutes": str(est),
        "guest_waitlist_position_ordinal_en": ordinal,
        "guest_waitlist_tables_required": str(tables_req),
        "guest_waitlist_can_seat_after_ahead": "yes" if feas else "no",
        "guest_waitlist_ahead_queue_feasible": "yes" if ahead_ok else "no",
        "guest_waitlist_seating_capacity_hint": seating_cap,
        "guest_waitlist_wait_time_hint": wait_hint,
    }


def _merge_waitlist_queue_into_profile(
    profile: dict[str, Any],
    *,
    queue_meta: dict[str, Any] | None,
) -> None:
    st = str(profile.get("reservation_seating_status") or "not_applicable")
    profile.update(
        _waitlist_queue_speech_variables(queue_meta=queue_meta, seating_status_resolved=st)
    )


def _demo_profile_for_caller(caller_number: str | None) -> dict[str, Any]:
    """Synthetic guests when DB has no row for this ANI."""
    normalized = (caller_number or "").strip()
    if normalized.endswith("0001"):
        profile = {
            "guest_display_name": "Jordan",
            "vip_tier": "gold",
            "preferred_venue_slug": "harbor-bistro",
            "default_party_size": 4,
            "preferred_locale": "en",
            "locale_hint": assistant_locale_hint("en"),
            "has_upcoming_reservation": True,
            "reservation_preorder_summary": "none",
            "reservation_food_subtotal_cents": 0,
            "reservation_preorder_discount_cents": 0,
            "reservation_food_total_cents": 0,
            "reservation_food_total_display": "$0.00",
            "reservation_has_preorder": False,
            "reservation_source_channel": "demo",
        }
        profile.update(_premium_concierge_variables(food_total_cents=0))
        profile.update(
            _seating_waitlist_profile(
                food_total_cents=0,
                guest_priority_raw="normal",
                seating_status_raw="not_applicable",
            )
        )
        _merge_waitlist_queue_into_profile(profile, queue_meta=None)
        return profile
    # Demo: ANI ending 0009 — premium pre-order ($550+ food total) for dynamic-variables / concierge flow tests.
    if normalized.endswith("0009"):
        total = 55_000
        profile = {
            "guest_display_name": "Alex",
            "vip_tier": "gold",
            "preferred_venue_slug": "hanok-table",
            "default_party_size": 6,
            "preferred_locale": "ko",
            "locale_hint": assistant_locale_hint("ko"),
            "has_upcoming_reservation": True,
            "next_reservation_code": "HNK-DEMO9",
            "next_reservation_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "reservation_preorder_summary": "Hanwoo BBQ set x4; bulgogi x2 (demo)",
            "reservation_food_subtotal_cents": total,
            "reservation_preorder_discount_cents": 0,
            "reservation_food_total_cents": total,
            "reservation_food_total_display": _food_display(total),
            "reservation_has_preorder": True,
            "reservation_source_channel": "demo",
        }
        profile.update(_premium_concierge_variables(food_total_cents=total))
        profile.update(
            _seating_waitlist_profile(
                food_total_cents=total,
                guest_priority_raw="normal",
                seating_status_raw="waitlist",
            )
        )
        per = hanok_waitlist_minutes_per_position()
        demo_meta = (
            {
                "position": 1,
                "queue_size": 1,
                "estimated_wait_minutes": per,
                "tables_required": 1,
                "feasible_after_ahead": True,
                "ahead_chain_ok": True,
            }
            if hanok_table_allocation_enabled()
            else None
        )
        _merge_waitlist_queue_into_profile(profile, queue_meta=demo_meta)
        return profile
    profile = {
        "guest_display_name": "Guest",
        "vip_tier": "standard",
        "preferred_venue_slug": "harbor-bistro",
        "default_party_size": 2,
        "preferred_locale": "en",
        "locale_hint": assistant_locale_hint("en"),
        "has_upcoming_reservation": False,
        "reservation_preorder_summary": "none",
        "reservation_food_subtotal_cents": 0,
        "reservation_preorder_discount_cents": 0,
        "reservation_food_total_cents": 0,
        "reservation_food_total_display": "$0.00",
        "reservation_has_preorder": False,
        "reservation_source_channel": "demo",
    }
    profile.update(_premium_concierge_variables(food_total_cents=0))
    profile.update(
        _seating_waitlist_profile(
            food_total_cents=0,
            guest_priority_raw="normal",
            seating_status_raw="not_applicable",
        )
    )
    _merge_waitlist_queue_into_profile(profile, queue_meta=None)
    return profile


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
                out = {
                    "guest_display_name": first,
                    "vip_tier": "returning",
                    "preferred_venue_slug": "hanok-table",
                    "default_party_size": any_row.party_size,
                    **(_locale_profile_fields(any_row)),
                    "has_upcoming_reservation": False,
                    # Same key as the upcoming branch so HTTP tools can bind confirmation_code even
                    # when the only row in range is in the past (e.g. modify pre-order after the meal).
                    "next_reservation_code": any_row.confirmation_code,
                    "next_reservation_at": any_row.starts_at.isoformat(),
                    "reservation_preorder_summary": summary_past or "none",
                    "reservation_food_subtotal_cents": any_row.food_subtotal_cents,
                    "reservation_preorder_discount_cents": any_row.preorder_discount_cents,
                    "reservation_food_total_cents": any_row.food_total_cents,
                    "reservation_food_total_display": _food_display(any_row.food_total_cents),
                    "reservation_has_preorder": bool(lines_past),
                    "reservation_source_channel": any_row.source_channel,
                }
                out.update(_premium_concierge_variables(food_total_cents=int(any_row.food_total_cents)))
                out.update(
                    _seating_waitlist_profile(
                        food_total_cents=int(any_row.food_total_cents),
                        guest_priority_raw=any_row.guest_priority,
                        seating_status_raw=any_row.seating_status,
                    )
                )
                qm = (
                    waitlist_queue_metadata(db, any_row)
                    if out.get("reservation_seating_status") == "waitlist"
                    else None
                )
                _merge_waitlist_queue_into_profile(out, queue_meta=qm)
                return out
            return None
        first = row.guest_name.split()[0] if row.guest_name else "Guest"
        lines = row.preorder_items
        summary = preorder_summary_text(lines)
        out = {
            "guest_display_name": first,
            "vip_tier": "confirmed_guest",
            "preferred_venue_slug": "hanok-table",
            "default_party_size": row.party_size,
            **(_locale_profile_fields(row)),
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
        out.update(_premium_concierge_variables(food_total_cents=int(row.food_total_cents)))
        out.update(
            _seating_waitlist_profile(
                food_total_cents=int(row.food_total_cents),
                guest_priority_raw=row.guest_priority,
                seating_status_raw=row.seating_status,
            )
        )
        qm = (
            waitlist_queue_metadata(db, row) if out.get("reservation_seating_status") == "waitlist" else None
        )
        _merge_waitlist_queue_into_profile(out, queue_meta=qm)
        return out
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

    # Telnyx may deliver hangup before speak.ended; drop the leg so we do not POST hangup on a dead call.
    if event_type == "call.hangup" or (isinstance(event_type, str) and event_type.endswith(".hangup")):
        with _hanok_cc_lock:
            _hanok_cc_ids.discard(call_control_id)
        return {"status": "ok"}

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


def _enrich_caller_identification_for_profile(profile: dict[str, Any], caller: str | None) -> None:
    """Expose normalized ANI and multi-booking hints so the assistant binds tools without re-asking phone."""
    raw = (caller or "").strip()
    profile["caller_phone_telnyx"] = raw
    profile["caller_phone_normalized"] = to_e164_us(raw) if raw else ""
    profile["caller_line_reservation_count"] = "0"
    profile["caller_line_single_booking"] = "no"
    profile["caller_line_has_multiple_bookings"] = "no"
    profile["caller_line_booking_guest_names_hint"] = ""
    profile["guest_personalized_greeting_suggestion"] = ""
    profile["guest_lookup_name_for_tools"] = ""
    profile["guest_lookup_identification_hint"] = (
        "Caller phone was not in the webhook payload (bind Telnyx `telnyx_end_user_target` / "
        "`caller_number` to tools). Ask for both name and phone for GET /api/reservations/lookup."
    )

    if not raw:
        return

    if not database_url():
        profile["guest_lookup_identification_hint"] = (
            f"Use caller_phone_normalized ({profile['caller_phone_normalized'] or raw}) as guest_phone on lookup tools. "
            "Do not ask the caller to repeat their number unless this value is empty. "
            "This deployment has no database URL for listing other bookings on the same line."
        )
        return

    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        return

    db = SessionLocal()
    try:
        pool = reservation_candidates_for_caller_line(db, raw)
    finally:
        db.close()

    if not pool:
        profile["guest_lookup_identification_hint"] = (
            "Phone is known from the network (caller_phone_normalized) but no reservation row matches yet. "
            "Use this phone on create/lookup; only ask the guest for their name once you need to disambiguate."
        )
        return

    profile["caller_line_reservation_count"] = str(len(pool))
    multi = len(pool) > 1
    profile["caller_line_has_multiple_bookings"] = "yes" if multi else "no"
    profile["caller_line_single_booking"] = "no" if multi else "yes"
    parts: list[str] = []
    for r in pool[:12]:
        g = (r.guest_name or "").strip() or "Guest"
        parts.append(f"{g} ({r.confirmation_code})")
    profile["caller_line_booking_guest_names_hint"] = "; ".join(parts)

    if multi:
        profile["guest_personalized_greeting_suggestion"] = ""
        profile["guest_lookup_name_for_tools"] = ""
        profile["guest_lookup_identification_hint"] = (
            "The caller's phone is already known from the carrier (caller_phone_normalized / caller_phone_telnyx). "
            "Do not ask them to repeat their phone number for lookup or amendments. "
            "On GET /api/reservations/lookup or GET /api/reservations/lookup-by-phone, set query parameter "
            "`phone` or `guest_phone` to caller_phone_normalized. Ask only for the first or full guest name "
            "on the booking (multiple reservations share this line). Names on file include: "
            f"{profile['caller_line_booking_guest_names_hint']}. "
            "After they give a name, pass it as `guest_name` on the same lookup call."
        )
    else:
        disp = str(profile.get("guest_display_name") or "").strip()
        if not disp:
            disp = (pool[0].guest_name or "").split()[0] if pool[0].guest_name else "Guest"
        profile["guest_personalized_greeting_suggestion"] = (
            f"Hi {disp}, thanks for calling Hanok Table. How can I help with your reservation today?"
        )
        profile["guest_lookup_name_for_tools"] = (pool[0].guest_name or "").strip() or disp
        profile["guest_lookup_identification_hint"] = (
            "Exactly one reservation matches this phone number (caller_line_single_booking is yes). "
            "Open immediately with guest_personalized_greeting_suggestion or guest_display_name—do NOT ask for their "
            "name before the first lookup tool call. "
            "MCP: call get_reservation with ONLY guest_phone set to caller_phone_normalized (omit guest_name entirely) "
            "so the API uses phone-only lookup. HTTP GET /api/reservations/lookup-by-phone?guest_phone=… also works. "
            "guest_lookup_name_for_tools is for reference or rare flows; you should not need to prompt the caller for it."
        )


@router.post("/variables")
async def dynamic_webhook_variables(
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Return personalization variables for the AI Assistant instruction templates.

    Premium pre-order detection uses the reservation row tied to the caller (upcoming, else most recent):
    see ``concierge_service_hint`` and ``guest_is_high_value_preorder`` (yes/no) for VIP-style prompts.
    """
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
    _enrich_caller_identification_for_profile(profile, caller)
    return profile
