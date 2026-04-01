"""High-value pre-order: first cancel attempt is 409 until retention_offer_acknowledged."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from telnyx_restaurant.models import Reservation
from telnyx_restaurant.routers.reservations import (
    _patch_at_status_core,
    _raise_if_premium_cancel_blocked,
)


def test_premium_cancel_blocked_without_ack(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_PREMIUM_CANCEL_RETENTION_GATE", "1")
    monkeypatch.setenv("HANOK_PREMIUM_PREORDER_CENTS", "30000")
    row = Reservation(
        confirmation_code="HNK-GTE1",
        guest_name="Mary",
        guest_phone="+19259897818",
        party_size=4,
        starts_at=datetime(2026, 8, 10, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json='[{"menu_item_id":"x","quantity":1}]',
        food_subtotal_cents=356_00,
        preorder_discount_cents=24_92,
        food_total_cents=331_08,
    )
    db_session.add(row)
    db_session.flush()
    flat = {"status": "cancelled"}
    with pytest.raises(HTTPException) as ei:
        _patch_at_status_core(db_session, row, flat)
    assert ei.value.status_code == 409
    d = ei.value.detail
    assert isinstance(d, dict)
    assert d.get("code") == "premium_cancel_requires_retention_step"
    db_session.refresh(row)
    assert row.status == "confirmed"


def test_premium_cancel_succeeds_with_ack(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_PREMIUM_CANCEL_RETENTION_GATE", "1")
    monkeypatch.setenv("HANOK_PREMIUM_PREORDER_CENTS", "30000")
    row = Reservation(
        confirmation_code="HNK-GTE2",
        guest_name="Mary",
        guest_phone="+19259897818",
        party_size=4,
        starts_at=datetime(2026, 8, 11, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json=None,
        food_subtotal_cents=0,
        preorder_discount_cents=0,
        food_total_cents=331_08,
    )
    db_session.add(row)
    db_session.flush()
    flat = {"status": "cancelled", "retention_offer_acknowledged": True}
    _, changed = _patch_at_status_core(db_session, row, flat)
    assert changed is True
    db_session.refresh(row)
    assert row.status == "cancelled"


def test_below_threshold_cancel_without_ack(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_PREMIUM_CANCEL_RETENTION_GATE", "1")
    monkeypatch.setenv("HANOK_PREMIUM_PREORDER_CENTS", "30000")
    row = Reservation(
        confirmation_code="HNK-GTE3",
        guest_name="X",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=datetime(2026, 8, 12, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json=None,
        food_subtotal_cents=0,
        preorder_discount_cents=0,
        food_total_cents=50_00,
    )
    db_session.add(row)
    db_session.flush()
    flat = {"status": "cancelled"}
    _, changed = _patch_at_status_core(db_session, row, flat)
    assert changed is True
    assert row.status == "cancelled"


def test_gate_disabled_allows_cancel(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_PREMIUM_CANCEL_RETENTION_GATE", "0")
    monkeypatch.setenv("HANOK_PREMIUM_PREORDER_CENTS", "30000")
    row = Reservation(
        confirmation_code="HNK-GTE4",
        guest_name="Y",
        guest_phone="+15550002222",
        party_size=2,
        starts_at=datetime(2026, 8, 13, 18, 0, tzinfo=UTC),
        status="confirmed",
        food_total_cents=400_00,
    )
    db_session.add(row)
    db_session.flush()
    flat = {"status": "cancelled"}
    _raise_if_premium_cancel_blocked(row, flat)
    _, changed = _patch_at_status_core(db_session, row, flat)
    assert changed is True


def test_raise_helper_no_op_when_not_premium(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_PREMIUM_CANCEL_RETENTION_GATE", "1")
    row = Reservation(
        confirmation_code="HNK-GTE5",
        guest_name="Z",
        guest_phone="+15550003333",
        party_size=2,
        starts_at=datetime(2026, 8, 14, 18, 0, tzinfo=UTC),
        status="confirmed",
        food_total_cents=100,
    )
    _raise_if_premium_cancel_blocked(row, {})
