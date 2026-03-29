"""Voice duplicate POST /api/reservations: same phone/slot/party within window → one row."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telnyx_restaurant.models import Reservation
from telnyx_restaurant.routers.reservations import _voice_create_recent_duplicate
from telnyx_restaurant.schemas_res import PreorderLineIn, ReservationCreate, ReservationUpdate


def _voice_row(**kwargs: object) -> Reservation:
    t = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)
    defaults: dict = {
        "confirmation_code": "HNK-DED1",
        "guest_name": "Kim",
        "guest_phone": "+15550001111",
        "party_size": 2,
        "starts_at": t,
        "status": "confirmed",
        "preorder_json": None,
        "food_subtotal_cents": 0,
        "preorder_discount_cents": 0,
        "food_total_cents": 0,
        "source_channel": "voice",
        "duration_minutes": 120,
        "guest_priority": "normal",
        "seating_status": "not_applicable",
        "preferred_locale": "en",
    }
    defaults.update(kwargs)
    return Reservation(**defaults)


def test_voice_dedup_finds_recent_match(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_VOICE_CREATE_DEDUP_SECONDS", "300")
    t = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)
    row = _voice_row()
    db_session.add(row)
    db_session.commit()

    body = ReservationCreate(
        guest_name="Kim",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=t,
        source_channel="voice",
    )
    dup = _voice_create_recent_duplicate(
        db_session,
        body=body,
        starts_at=t,
        guest_phone_e164="+15550001111",
        window_seconds=300,
    )
    assert dup is not None
    assert dup.id == row.id


def test_voice_dedup_skips_cancelled(db_session) -> None:
    t = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)
    row = _voice_row(status="cancelled")
    db_session.add(row)
    db_session.commit()

    body = ReservationCreate(
        guest_name="Kim",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=t,
        source_channel="voice",
    )
    dup = _voice_create_recent_duplicate(
        db_session,
        body=body,
        starts_at=t,
        guest_phone_e164="+15550001111",
        window_seconds=300,
    )
    assert dup is None


def test_voice_dedup_skips_online_channel(db_session) -> None:
    t = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)
    row = _voice_row(source_channel="voice")
    db_session.add(row)
    db_session.commit()

    body = ReservationCreate(
        guest_name="Kim",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=t,
        source_channel="online",
    )
    dup = _voice_create_recent_duplicate(
        db_session,
        body=body,
        starts_at=t,
        guest_phone_e164="+15550001111",
        window_seconds=300,
    )
    assert dup is None


def test_voice_dedup_merge_preorder_on_stub(db_session) -> None:
    from telnyx_restaurant.routers.reservations import _apply_reservation_update

    t = datetime(2026, 6, 15, 19, 0, tzinfo=UTC)
    row = _voice_row()
    db_session.add(row)
    db_session.commit()

    body = ReservationCreate(
        guest_name="Kim",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=t,
        source_channel="voice",
        preorder=[PreorderLineIn(menu_item_id="bulgogi", quantity=1)],
    )
    dup = _voice_create_recent_duplicate(
        db_session,
        body=body,
        starts_at=t,
        guest_phone_e164="+15550001111",
        window_seconds=300,
    )
    assert dup is not None
    patch = ReservationUpdate(preorder=body.preorder)
    assert _apply_reservation_update(db_session, dup, patch) is True
    db_session.commit()
    db_session.refresh(dup)
    assert dup.preorder_items
    assert dup.food_total_cents > 0
