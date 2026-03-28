"""Regression: nested payloads must not wipe confirmation_code / id with JSON null."""

from __future__ import annotations

from telnyx_restaurant.schemas_res import _unwrap_nested_reservation_payload


def test_unwrap_preserves_confirmation_code_when_inner_body_has_null() -> None:
    raw = {
        "confirmation_code": "HNK-ABCD",
        "body": {
            "preorder": [{"menu_item_id": "bulgogi", "quantity": 1}],
            "confirmation_code": None,
            "guest_name": "HJ",
        },
    }
    flat = _unwrap_nested_reservation_payload(raw)
    assert flat.get("confirmation_code") == "HNK-ABCD"
    assert isinstance(flat.get("preorder"), list)
    assert flat["preorder"][0]["menu_item_id"] == "bulgogi"


def test_unwrap_preserves_reservation_id_when_inner_null() -> None:
    raw = {
        "reservation_id": 11,
        "parameters": {
            "preorder": [{"menu_item_id": "dolsot_bibimbap", "quantity": 2}],
            "reservation_id": None,
        },
    }
    flat = _unwrap_nested_reservation_payload(raw)
    assert flat.get("reservation_id") == 11
