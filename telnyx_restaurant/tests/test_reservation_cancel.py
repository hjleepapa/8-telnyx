"""Cancel via PATCH: Telnyx null placeholders must not block status-only updates."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from telnyx_restaurant.models import Reservation
from telnyx_restaurant.routers.reservations import (
    _apply_reservation_update,
    _reject_modifying_cancelled,
    _truthy_non_status_reservation_fields,
)
from telnyx_restaurant.schemas_res import ReservationUpdate


def test_truthy_non_status_false_when_only_cancel_and_null_placeholders() -> None:
    body = ReservationUpdate.model_validate(
        {
            "status": "cancelled",
            "party_size": None,
            "starts_at": None,
            "preorder": None,
            "guest_name": None,
        }
    )
    assert _truthy_non_status_reservation_fields(body) is False


def test_truthy_non_status_true_when_party_changed() -> None:
    body = ReservationUpdate.model_validate({"status": "cancelled", "party_size": 3})
    assert _truthy_non_status_reservation_fields(body) is True


def test_cancel_confirmed_with_null_template_applies(db_session) -> None:
    from datetime import UTC, datetime

    row = Reservation(
        confirmation_code="HNK-CAN1",
        guest_name="X",
        guest_phone="+15550003333",
        party_size=2,
        starts_at=datetime(2026, 8, 1, 18, 0, tzinfo=UTC),
        status="confirmed",
        preorder_json=None,
        food_subtotal_cents=0,
        preorder_discount_cents=0,
        food_total_cents=0,
    )
    db_session.add(row)
    db_session.flush()
    body = ReservationUpdate.model_validate(
        {
            "status": "cancelled",
            "party_size": None,
            "preorder": None,
        }
    )
    assert _truthy_non_status_reservation_fields(body) is False
    assert _apply_reservation_update(db_session, row, body) is True
    assert row.status == "cancelled"


def test_cancel_again_template_does_not_count_nulls_as_non_status_patch() -> None:
    body = ReservationUpdate.model_validate(
        {"status": "cancelled", "party_size": None, "preorder": None}
    )
    assert _truthy_non_status_reservation_fields(body) is False


def test_reject_when_cancelled_and_party_patch(db_session) -> None:
    from datetime import UTC, datetime

    row = Reservation(
        confirmation_code="HNK-CAN3",
        guest_name="Z",
        guest_phone="+15550005555",
        party_size=2,
        starts_at=datetime(2026, 8, 3, 18, 0, tzinfo=UTC),
        status="cancelled",
        preorder_json=None,
        food_subtotal_cents=0,
        preorder_discount_cents=0,
        food_total_cents=0,
    )
    db_session.add(row)
    db_session.flush()
    body = ReservationUpdate.model_validate({"party_size": 6})
    assert _truthy_non_status_reservation_fields(body) is True
    with pytest.raises(HTTPException) as ei:
        _reject_modifying_cancelled(row)
    assert ei.value.status_code == 409
