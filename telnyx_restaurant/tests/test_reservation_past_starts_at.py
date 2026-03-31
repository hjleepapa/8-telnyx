"""Reject reservation create / amend when starts_at is before current time (UTC)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from telnyx_restaurant.models import Reservation
from telnyx_restaurant.routers import reservations as res_mod
from telnyx_restaurant.routers.reservations import _apply_reservation_update, _reject_if_starts_at_in_past
from telnyx_restaurant.schemas_res import ReservationUpdate


def test_reject_if_starts_at_in_past_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        res_mod,
        "_utc_now",
        lambda: datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
    )
    with pytest.raises(HTTPException) as exc_info:
        _reject_if_starts_at_in_past(datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
    assert exc_info.value.status_code == 400
    assert "past" in (exc_info.value.detail or "").lower()


def test_reject_if_starts_at_in_past_allows_now_and_future(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(res_mod, "_utc_now", lambda: fixed)
    _reject_if_starts_at_in_past(fixed)
    _reject_if_starts_at_in_past(datetime(2026, 6, 15, 12, 0, 1, tzinfo=UTC))


def test_patch_to_past_starts_at_rejected(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        res_mod,
        "_utc_now",
        lambda: datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
    )
    row = Reservation(
        confirmation_code="HNK-PAST",
        guest_name="T",
        guest_phone="+15550004444",
        party_size=2,
        starts_at=datetime(2026, 6, 20, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json=None,
        food_subtotal_cents=0,
        preorder_discount_cents=0,
        food_total_cents=0,
    )
    db_session.add(row)
    db_session.flush()

    body = ReservationUpdate.model_validate(
        {"starts_at": "2026-06-10T18:00:00+00:00"},
    )
    with pytest.raises(HTTPException) as exc_info:
        _apply_reservation_update(db_session, row, body)
    assert exc_info.value.status_code == 400
