"""Demo: schedule an outbound Telnyx reminder ~5s after a reservation is stored."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from fastapi import BackgroundTasks

from telnyx_restaurant.config import database_url, telnyx_api_key, telnyx_connection_id, telnyx_from_number

logger = logging.getLogger(__name__)


def schedule_demo_reminder_call(
    background_tasks: BackgroundTasks,
    *,
    reservation_id: int,
    guest_phone: str,
    guest_name: str,
    confirmation_code: str,
) -> None:
    background_tasks.add_task(
        _demo_reminder_worker,
        reservation_id,
        guest_phone.strip(),
        guest_name.strip(),
        confirmation_code,
    )


def _demo_reminder_worker(
    reservation_id: int,
    guest_phone: str,
    guest_name: str,
    confirmation_code: str,
) -> None:
    time.sleep(5)
    first = (guest_name or "Guest").split()[0]
    status = _place_telnyx_reminder_call(
        to_e164=guest_phone,
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
    from_num = telnyx_from_number()
    if not key or not conn or not from_num:
        logger.info(
            "Demo reminder (no Telnyx dial env): would call %s for reservation %s",
            to_e164,
            confirmation_code,
        )
        return "demo_skipped_no_telnyx_config"

    body: dict[str, str] = {
        "connection_id": conn,
        "to": to_e164,
        "from": from_num,
        "client_state": json.dumps(
            {
                "hanok_reminder": True,
                "confirmation_code": confirmation_code,
                "guest_first_name": guest_first_name,
            }
        ),
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
        return "telnyx_call_initiated"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        logger.warning("Telnyx reminder HTTP %s: %s", e.code, detail)
        return f"telnyx_error_http_{e.code}"
    except Exception:
        logger.exception("Telnyx reminder call failed")
        return "telnyx_error_exception"
