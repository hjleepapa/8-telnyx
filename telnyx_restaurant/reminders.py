"""Demo: schedule an outbound Telnyx reminder ~5s after a reservation is stored."""

from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Any

from telnyx_restaurant.config import (
    database_url,
    hanok_reminder_delay_seconds,
    telnyx_api_key,
    telnyx_connection_id,
    telnyx_from_number,
)
from telnyx_restaurant.phone_normalize import to_e164_us
from telnyx_restaurant.preorder_calc import preorder_summary_text

logger = logging.getLogger(__name__)


def build_reminder_speak_text(state: dict[str, Any]) -> str:
    """Spoken script for outbound Call Control TTS (Hanok reminder)."""
    first = state.get("guest_first_name") or "there"
    code = state.get("confirmation_code") or ""
    party = state.get("party_size")
    when = (state.get("starts_at_speech") or "").strip()
    food = (state.get("preorder_summary") or "").strip()

    parts: list[str] = [
        f"Hello {first}, this is Hanok Table with a reminder about your reservation.",
    ]
    if party is not None:
        parts.append(f"Your party size is {party}.")
    if when:
        parts.append(f"Your reservation time is {when}.")
    if food:
        parts.append(f"Your pre-order includes {food}.")
    parts.append(f"Your confirmation code is {code}.")
    parts.append("If you need to make changes, please call the restaurant. We look forward to seeing you.")
    return " ".join(parts)


def schedule_demo_reminder_call(
    *,
    reservation_id: int,
    guest_phone: str,
    guest_name: str,
    confirmation_code: str,
) -> None:
    """Use a thread timer so the job still runs after the HTTP response (BackgroundTasks can be flaky)."""

    gp = guest_phone.strip()
    gn = guest_name.strip()
    cc = confirmation_code

    def _fire() -> None:
        try:
            _demo_reminder_worker(reservation_id, gp, gn, cc)
        except Exception:
            logger.exception("Hanok reminder worker crashed for reservation_id=%s", reservation_id)

    delay = hanok_reminder_delay_seconds()
    logger.info(
        "Hanok: demo reminder scheduled in %.1fs (reservation_id=%s code=%s to=%s)",
        delay,
        reservation_id,
        cc,
        gp,
    )
    t = threading.Timer(delay, _fire)
    t.daemon = True
    t.start()


def _demo_reminder_worker(
    reservation_id: int,
    guest_phone: str,
    guest_name: str,
    confirmation_code: str,
) -> None:
    first = (guest_name or "Guest").split()[0]
    to_e164 = to_e164_us(guest_phone)

    state_obj: dict[str, Any] = {
        "hanok_reminder": True,
        "confirmation_code": confirmation_code,
        "guest_first_name": first,
        "guest_full_name": (guest_name or "").strip(),
        "party_size": None,
        "starts_at_speech": "",
        "preorder_summary": "",
    }

    if database_url():
        from telnyx_restaurant.db import SessionLocal, get_engine
        from telnyx_restaurant.models import Reservation

        get_engine()
        if SessionLocal is not None:
            db = SessionLocal()
            try:
                row = db.get(Reservation, reservation_id)
                if row:
                    state_obj["party_size"] = row.party_size
                    dt = row.starts_at
                    if dt.tzinfo:
                        state_obj["starts_at_speech"] = dt.strftime("%A, %B %d at %I:%M %p %Z")
                    else:
                        state_obj["starts_at_speech"] = dt.strftime("%A, %B %d at %I:%M %p")
                    summ = preorder_summary_text(row.preorder_items)
                    if len(summ) > 480:
                        summ = summ[:477].rsplit(";", 1)[0] + ", and more."
                    state_obj["preorder_summary"] = summ
            except Exception:
                logger.exception("Hanok reminder: failed to load reservation for speech payload")
            finally:
                db.close()

    status = _place_telnyx_reminder_call(to_e164=to_e164, state_obj=state_obj)
    _persist_reminder_status(reservation_id, status)


def _persist_reminder_status(reservation_id: int, status: str) -> None:
    from telnyx_restaurant.db import SessionLocal, get_engine
    from telnyx_restaurant.models import Reservation

    if not database_url():
        return
    get_engine()
    if SessionLocal is None:
        return
    db = SessionLocal()
    try:
        row = db.get(Reservation, reservation_id)
        if row:
            row.reminder_call_status = status[:120]
            db.commit()
    except Exception:
        logger.exception("Failed to persist reminder_call_status")
    finally:
        db.close()


def _place_telnyx_reminder_call(
    *,
    to_e164: str,
    state_obj: dict[str, Any],
) -> str:
    key = telnyx_api_key()
    conn = telnyx_connection_id()
    from_raw = telnyx_from_number()
    if not key or not conn or not from_raw:
        logger.warning(
            "Hanok demo reminder skipped: set TELNYX_API_KEY, TELNYX_CONNECTION_ID (Call Control App id), "
            "and TELNYX_FROM_NUMBER on the server. Would have dialed %s for %s.",
            to_e164,
            state_obj.get("confirmation_code"),
        )
        return "demo_skipped_no_telnyx_config"

    to_norm = to_e164_us(to_e164)
    from_norm = to_e164_us(from_raw)
    if not to_norm.startswith("+") or len(to_norm) < 10:
        logger.warning("Hanok reminder: invalid destination E.164: %r", to_e164)
        return "demo_skipped_bad_destination"

    payload = dict(state_obj)
    payload["hanok_reminder"] = True
    # Telnyx Call Control expects client_state as base64-encoded payload
    client_state = base64.b64encode(json.dumps(payload, default=str).encode("utf-8")).decode("ascii")

    body: dict[str, str] = {
        "connection_id": conn,
        "to": to_norm,
        "from": from_norm,
        "client_state": client_state,
    }
    try:
        req = urllib.request.Request(
            "https://api.telnyx.com/v2/calls",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            resp.read()
        logger.info(
            "Hanok reminder: Telnyx POST /v2/calls accepted for %s",
            state_obj.get("confirmation_code"),
        )
        return "telnyx_call_initiated"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        logger.warning("Hanok reminder: Telnyx HTTP %s: %s", e.code, detail)
        return f"telnyx_error_http_{e.code}"
    except Exception:
        logger.exception("Hanok reminder: Telnyx call failed")
        return "telnyx_error_exception"


def telnyx_speak(call_control_id: str, text: str) -> tuple[bool, str]:
    """Play TTS on an answered Call Control leg (requires webhook to trigger on call.answered)."""
    key = telnyx_api_key()
    if not key or not call_control_id:
        return False, "missing_config"
    attempts = (
        {
            "payload": text,
            "payload_type": "text",
            "voice": "AWS.Polly.Joanna",
            "language": "en-US",
        },
        {"payload": text, "payload_type": "text", "voice": "female", "language": "en-US"},
        {"payload": text},
        {"text": text},
    )
    last_err = "exception"
    for body in attempts:
        try:
            req = urllib.request.Request(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/speak",
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return True, "ok"
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:800]
            logger.warning("Hanok speak: HTTP %s %s", e.code, detail)
            last_err = f"http_{e.code}"
        except Exception:
            logger.exception("Hanok speak failed")
            last_err = "exception"
    return False, last_err


def telnyx_hangup(call_control_id: str) -> bool:
    key = telnyx_api_key()
    if not key or not call_control_id:
        return False
    try:
        req = urllib.request.Request(
            f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/hangup",
            data=json.dumps({}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception:
        logger.exception("Hanok hangup failed")
        return False
