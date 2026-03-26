"""Demo: schedule an outbound Telnyx reminder ~5s after a reservation is stored."""

from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.error
import urllib.request

from telnyx_restaurant.config import database_url, telnyx_api_key, telnyx_connection_id, telnyx_from_number
from telnyx_restaurant.phone_normalize import to_e164_us

logger = logging.getLogger(__name__)


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

    logger.info(
        "Hanok: demo reminder scheduled in 5s (reservation_id=%s code=%s to=%s)",
        reservation_id,
        cc,
        gp,
    )
    t = threading.Timer(5.0, _fire)
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
    status = _place_telnyx_reminder_call(
        to_e164=to_e164,
        confirmation_code=confirmation_code,
        guest_first_name=first,
    )
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
    confirmation_code: str,
    guest_first_name: str,
) -> str:
    key = telnyx_api_key()
    conn = telnyx_connection_id()
    from_raw = telnyx_from_number()
    if not key or not conn or not from_raw:
        logger.warning(
            "Hanok demo reminder skipped: set TELNYX_API_KEY, TELNYX_CONNECTION_ID (Call Control App id), "
            "and TELNYX_FROM_NUMBER on the server. Would have dialed %s for %s.",
            to_e164,
            confirmation_code,
        )
        return "demo_skipped_no_telnyx_config"

    to_norm = to_e164_us(to_e164)
    from_norm = to_e164_us(from_raw)
    if not to_norm.startswith("+") or len(to_norm) < 10:
        logger.warning("Hanok reminder: invalid destination E.164: %r", to_e164)
        return "demo_skipped_bad_destination"

    state_obj = {
        "hanok_reminder": True,
        "confirmation_code": confirmation_code,
        "guest_first_name": guest_first_name,
    }
    # Telnyx Call Control expects client_state as base64-encoded payload
    client_state = base64.b64encode(json.dumps(state_obj).encode("utf-8")).decode("ascii")

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
        logger.info("Hanok reminder: Telnyx POST /v2/calls accepted for %s", confirmation_code)
        return "telnyx_call_initiated"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:800]
        logger.warning("Hanok reminder: Telnyx HTTP %s: %s", e.code, detail)
        return f"telnyx_error_http_{e.code}"
    except Exception:
        logger.exception("Hanok reminder: Telnyx call failed")
        return "telnyx_error_exception"
