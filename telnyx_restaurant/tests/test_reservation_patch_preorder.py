"""PATCH semantics: Telnyx null placeholder on preorder must not clear cart when party/time update."""

from __future__ import annotations

from datetime import UTC, datetime

from telnyx_restaurant.models import Reservation
from telnyx_restaurant.routers.reservations import _apply_reservation_update
from telnyx_restaurant.schemas_res import ReservationUpdate


def test_party_and_time_with_preorder_null_preserves_cart(db_session) -> None:
    row = Reservation(
        confirmation_code="HNK-PRE1",
        guest_name="T",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=datetime(2026, 6, 1, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json='[{"menu_item_id":"bulgogi","quantity":1,"name_en":"Bulgogi","line_total_cents":2500}]',
        food_subtotal_cents=2500,
        preorder_discount_cents=0,
        food_total_cents=2500,
    )
    db_session.add(row)
    db_session.flush()

    body = ReservationUpdate.model_validate(
        {
            "party_size": 4,
            "starts_at": "2026-06-01T19:00:00+00:00",
            "preorder": None,
        }
    )
    assert _apply_reservation_update(db_session, row, body) is True
    assert row.party_size == 4
    assert row.preorder_json is not None
    assert "bulgogi" in row.preorder_json
    assert row.food_total_cents == 2500


def test_telnyx_all_null_template_does_not_clear_cart(db_session) -> None:
    """Voice HTTP tools send every amend key with JSON null; preorder null is not 'clear cart'."""
    row = Reservation(
        confirmation_code="HNK-PRE3",
        guest_name="T",
        guest_phone="+15550003333",
        party_size=2,
        starts_at=datetime(2026, 6, 1, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json='[{"menu_item_id":"bulgogi","quantity":1}]',
        food_subtotal_cents=100,
        preorder_discount_cents=0,
        food_total_cents=100,
    )
    db_session.add(row)
    db_session.flush()

    body = ReservationUpdate.model_validate(
        {
            "guest_name": None,
            "guest_phone": None,
            "party_size": None,
            "preorder": None,
            "special_requests": None,
            "starts_at": None,
        }
    )
    assert _apply_reservation_update(db_session, row, body) is False
    assert row.preorder_json is not None
    assert row.food_total_cents == 100


def test_preorder_null_only_still_clears_cart(db_session) -> None:
    row = Reservation(
        confirmation_code="HNK-PRE2",
        guest_name="T",
        guest_phone="+15550002222",
        party_size=2,
        starts_at=datetime(2026, 6, 1, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json='[{"menu_item_id":"bulgogi","quantity":1}]',
        food_subtotal_cents=100,
        preorder_discount_cents=0,
        food_total_cents=100,
    )
    db_session.add(row)
    db_session.flush()

    body = ReservationUpdate.model_validate({"preorder": None})
    assert _apply_reservation_update(db_session, row, body) is True
    assert row.preorder_json is None
    assert row.food_subtotal_cents == 0
    assert row.food_total_cents == 0
