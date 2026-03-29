"""Premium pre-order fields on dynamic webhook variables (concierge tier by food_total_cents)."""

from __future__ import annotations

from telnyx_restaurant.routers.webhook import _premium_concierge_variables


def test_premium_below_threshold_is_standard() -> None:
    v = _premium_concierge_variables(food_total_cents=49_999, threshold=50_000)
    assert v["guest_is_high_value_preorder"] == "no"
    assert v["guest_preorder_value_tier"] == "standard"
    assert "Standard guest" in v["concierge_service_hint"]


def test_premium_at_threshold_is_yes() -> None:
    v = _premium_concierge_variables(food_total_cents=50_000, threshold=50_000)
    assert v["guest_is_high_value_preorder"] == "yes"
    assert v["guest_preorder_value_tier"] == "premium_preorder"
    assert v["guest_preorder_total_crossed_premium_threshold"] == "yes"
    assert "High-value" in v["concierge_service_hint"]


def test_premium_disabled_when_threshold_zero() -> None:
    v = _premium_concierge_variables(food_total_cents=99_999, threshold=0)
    assert v["guest_is_high_value_preorder"] == "no"
