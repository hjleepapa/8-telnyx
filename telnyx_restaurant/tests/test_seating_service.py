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
    reseat_reservation_after_amend,
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


def test_promote_smaller_party_when_earlier_larger_cannot_fit(
    monkeypatch: pytest.MonkeyPatch, db_session: Session
) -> None:
    """Freed capacity may only fit a later waitlister with a smaller party_size."""
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "true")
    monkeypatch.setenv("HANOK_TABLE_INVENTORY_JSON", '{"4":1,"8":1}')
    monkeypatch.setenv("HANOK_TABLE_SLOT_MINUTES", "60")
    monkeypatch.setenv("HANOK_RESERVATION_DURATION_MINUTES", "60")
    monkeypatch.setenv("HANOK_MAX_TABLES_PER_PARTY", "2")
    start = datetime(2026, 7, 10, 19, 0, tzinfo=UTC)
    r1 = _row(code="HNK-T1", party=8, starts=start)
    r2 = _row(code="HNK-T2", party=4, starts=start)
    db_session.add_all([r1, r2])
    db_session.flush()
    book_on_create(db_session, r1, waitlist_ok=True)
    book_on_create(db_session, r2, waitlist_ok=True)
    assert r1.seating_status == "allocated"
    assert r2.seating_status == "allocated"

    r_big = _row(code="HNK-W8", party=8, starts=start, name="WaitBig", phone="+15550001001")
    r_small = _row(code="HNK-W4", party=4, starts=start, name="WaitSmall", phone="+15550001002")
    db_session.add_all([r_big, r_small])
    db_session.flush()
    book_on_create(db_session, r_big, waitlist_ok=True)
    book_on_create(db_session, r_small, waitlist_ok=True)
    assert r_big.seating_status == "waitlist"
    assert r_small.seating_status == "waitlist"

    release_and_promote_after_cancel(db_session, r2)
    db_session.refresh(r_big)
    db_session.refresh(r_small)
    assert r_small.seating_status == "allocated"
    assert json.loads(r_small.tables_allocated_json or "[]") == [4]
    assert r_big.seating_status == "waitlist"


def test_amend_reschedule_allocated_promotes_waitlist_at_old_slot(
    seating_env: None, db_session: Session
) -> None:
    """Changing starts_at must release the old slot and promote waitlisted parties there."""
    start_a = datetime(2026, 7, 20, 19, 0, tzinfo=UTC)
    start_b = datetime(2026, 7, 21, 19, 0, tzinfo=UTC)
    hold = _row(code="HNK-MV1", party=6, starts=start_a)
    wait = _row(code="HNK-MV2", party=6, starts=start_a, phone="+15550002222")
    db_session.add_all([hold, wait])
    db_session.flush()
    book_on_create(db_session, hold, waitlist_ok=True)
    book_on_create(db_session, wait, waitlist_ok=True)
    assert hold.seating_status == "allocated"
    assert wait.seating_status == "waitlist"

    old_starts = hold.starts_at
    old_party = hold.party_size
    old_dur = hold.duration_minutes
    old_seat = hold.seating_status
    old_json = hold.tables_allocated_json

    hold.starts_at = start_b
    reseat_reservation_after_amend(
        db_session,
        hold,
        old_starts_at=old_starts,
        old_party_size=old_party,
        old_duration_minutes=old_dur,
        old_seating_status=old_seat,
        old_tables_allocated_json=old_json,
    )
    db_session.refresh(wait)
    db_session.refresh(hold)
    assert wait.seating_status == "allocated"
    assert json.loads(wait.tables_allocated_json or "[]") == [6]
    assert hold.seating_status == "allocated"


def test_waitlist_promotion_schedules_reminder_for_voice(
    seating_env: None, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a waitlisted voice/online guest is promoted, queue the outbound reminder."""
    promoted_ids: list[int] = []

    def _capture(row: Reservation) -> None:
        promoted_ids.append(row.id)

    monkeypatch.setattr(
        "telnyx_restaurant.seating_service.schedule_reminder_on_table_allocated",
        _capture,
    )
    start = datetime(2026, 7, 25, 19, 0, tzinfo=UTC)
    hold = _row(code="HNK-RH1", party=6, starts=start)
    wait = _row(
        code="HNK-RW1",
        party=6,
        starts=start,
        phone="+15550003333",
    )
    wait.source_channel = "voice"
    db_session.add_all([hold, wait])
    db_session.flush()
    book_on_create(db_session, hold, waitlist_ok=True)
    book_on_create(db_session, wait, waitlist_ok=True)
    release_and_promote_after_cancel(db_session, hold)
    db_session.refresh(wait)
    assert wait.seating_status == "allocated"
    assert promoted_ids == [wait.id]
