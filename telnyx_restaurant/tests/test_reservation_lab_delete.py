"""Reservation lab: DELETE /api/reservations/{id} is gated by HANOK_RESERVATION_LAB."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.routers.reservations import delete_reservation_lab


def _row() -> Reservation:
    return Reservation(
        confirmation_code="HNK-LABD",
        guest_name="Lab",
        guest_phone="+15550007777",
        party_size=2,
        starts_at=datetime(2026, 10, 1, 19, 0, tzinfo=UTC),
        status=ReservationStatus.confirmed.value,
        source_channel="api",
        reminder_call_status=None,
    )


def test_delete_reservation_lab_404_when_disabled(monkeypatch: pytest.MonkeyPatch, db_session) -> None:
    monkeypatch.setenv("HANOK_RESERVATION_LAB", "0")
    r = _row()
    db_session.add(r)
    db_session.flush()
    rid = r.id
    with pytest.raises(HTTPException) as exc_info:
        delete_reservation_lab(str(rid), db=db_session)
    assert exc_info.value.status_code == 404
    assert db_session.get(Reservation, rid) is not None


def test_delete_reservation_lab_removes_row(monkeypatch: pytest.MonkeyPatch, db_session) -> None:
    monkeypatch.setenv("HANOK_RESERVATION_LAB", "1")
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "0")
    r = _row()
    db_session.add(r)
    db_session.flush()
    rid = r.id
    delete_reservation_lab(str(rid), db=db_session)
    assert db_session.get(Reservation, rid) is None
