"""Seating inventory + waitlist with sqlite."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.seating_service import (
    book_on_create,
    release_and_promote_after_cancel,
    try_allocate_and_consume,
)


def _row(
    *,
    code: str,
    party: int,
    starts: datetime,
    duration: int = 60,
    name: str = "Test",
    phone: str = "+15550001111",
) -> Reservation:
    return Reservation(
        confirmation_code=code,
        guest_name=name,
        guest_phone=phone,
        party_size=party,
        starts_at=starts,
        duration_minutes=duration,
        status=ReservationStatus.confirmed.value,
        guest_priority="normal",
        seating_status="not_applicable",
        source_channel="api",
        reminder_call_status=None,
    )


@pytest.fixture
def seating_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "true")
    # Only one 6-top per slot so party_size=6 cannot fall back to combining 4-tops.
    monkeypatch.setenv("HANOK_TABLE_INVENTORY_JSON", '{"6":1}')
    monkeypatch.setenv("HANOK_TABLE_SLOT_MINUTES", "60")
    monkeypatch.setenv("HANOK_RESERVATION_DURATION_MINUTES", "60")
    monkeypatch.setenv("HANOK_MAX_TABLES_PER_PARTY", "2")


def test_try_allocate_and_consume_multi_slot(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "true")
    monkeypatch.setenv("HANOK_TABLE_INVENTORY_JSON", '{"6":1}')
    monkeypatch.setenv("HANOK_TABLE_SLOT_MINUTES", "60")
    monkeypatch.setenv("HANOK_RESERVATION_DURATION_MINUTES", "120")
    start = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
    alloc = try_allocate_and_consume(db_session, 6, start, 120)
    assert alloc == [6]
    db_session.commit()


def test_book_waitlist_and_promote(seating_env: None, db_session: Session) -> None:
    start = datetime(2026, 7, 2, 19, 0, tzinfo=UTC)
    a = _row(code="HNK-AAA1", party=6, starts=start)
    db_session.add(a)
    db_session.flush()
    r1 = book_on_create(db_session, a, waitlist_ok=True)
    assert r1.seating_status == "allocated"

    b = _row(code="HNK-BBB2", party=6, starts=start)
    db_session.add(b)
    db_session.flush()
    r2 = book_on_create(db_session, b, waitlist_ok=True)
    assert r2.seating_status == "waitlist"

    release_and_promote_after_cancel(db_session, a)
    assert a.seating_status == "not_applicable"
    assert a.tables_allocated_json is None

    db_session.refresh(b)
    assert b.seating_status == "allocated"
    assert json.loads(b.tables_allocated_json or "[]") == [6]


def test_book_rejects_when_full(seating_env: None, db_session: Session) -> None:
    from telnyx_restaurant.seating_service import SeatingUnavailableError

    start = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    a = _row(code="HNK-CCC3", party=6, starts=start)
    db_session.add(a)
    db_session.flush()
    book_on_create(db_session, a, waitlist_ok=True)

    b = _row(code="HNK-DDD4", party=6, starts=start)
    db_session.add(b)
    db_session.flush()
    with pytest.raises(SeatingUnavailableError):
        book_on_create(db_session, b, waitlist_ok=False)


def test_promote_vip_before_normal(seating_env: None, db_session: Session) -> None:
    start = datetime(2026, 7, 4, 20, 0, tzinfo=UTC)
    hold = _row(code="HNK-HOLD", party=6, starts=start)
    db_session.add(hold)
    db_session.flush()
    book_on_create(db_session, hold, waitlist_ok=True)

    norm = _row(code="HNK-NORM", party=6, starts=start, name="Norm")
    norm.guest_priority = "normal"
    vip = _row(code="HNK-VIPP", party=6, starts=start, name="Vip")
    vip.guest_priority = "vip"
    db_session.add_all([norm, vip])
    db_session.flush()
    book_on_create(db_session, norm, waitlist_ok=True)
    book_on_create(db_session, vip, waitlist_ok=True)
    assert norm.seating_status == "waitlist"
    assert vip.seating_status == "waitlist"

    release_and_promote_after_cancel(db_session, hold)
    db_session.refresh(vip)
    db_session.refresh(norm)
    assert vip.seating_status == "allocated"
    assert norm.seating_status == "waitlist"
