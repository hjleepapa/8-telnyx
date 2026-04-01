"""Premium tier on shared phone lines uses max pre-order total across active bookings."""

from __future__ import annotations

from types import SimpleNamespace

from telnyx_restaurant.routers.webhook import _line_preorder_total_max_for_premium, _premium_concierge_variables


def test_line_preorder_total_max_for_premium_uses_largest_cart() -> None:
    pool = [
        SimpleNamespace(food_total_cents=5_000),
        SimpleNamespace(food_total_cents=33_108),
    ]
    assert _line_preorder_total_max_for_premium(pool) == 33_108


def test_premium_variables_yes_when_line_max_crosses_threshold() -> None:
    pool = [
        SimpleNamespace(food_total_cents=9_999),
        SimpleNamespace(food_total_cents=35_000),
    ]
    v = _premium_concierge_variables(food_total_cents=_line_preorder_total_max_for_premium(pool))
    assert v["guest_is_high_value_preorder"] == "yes"
    assert "complimentary" in v["cancel_retention_offer"].lower()
