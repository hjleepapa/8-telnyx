"""Telnyx template aliases and defensive premium backfill on dynamic variables."""

from __future__ import annotations

from telnyx_restaurant.routers.webhook import (
    _ensure_premium_concierge_on_profile,
    _premium_concierge_variables,
    _telnyx_template_alias_variants,
)


def test_ensure_premium_from_food_total_when_keys_missing() -> None:
    p: dict = {"reservation_food_total_cents": 35_000}
    _ensure_premium_concierge_on_profile(p)
    assert p["guest_preorder_value_tier"] == "premium_preorder"
    assert "cancel_retention_offer" in p


def test_ensure_skips_when_already_present() -> None:
    p: dict = {"reservation_food_total_cents": 0}
    p.update(_premium_concierge_variables(food_total_cents=50_000))
    _ensure_premium_concierge_on_profile(p)
    assert p["guest_preorder_value_tier"] == "premium_preorder"


def test_telnyx_alias_variants_mirror_values() -> None:
    p: dict = {
        "reservation_food_total_cents": 35_000,
        "reservation_food_total_display": "$350.00",
    }
    p.update(_premium_concierge_variables(food_total_cents=35_000))
    _telnyx_template_alias_variants(p)
    assert p["cancelRetentionOffer"] == p["cancel_retention_offer"]
    assert p["guestPreorderValueTier"] == p["guest_preorder_value_tier"]
    assert p["food_total_cents"] == 35_000
    assert p["crossed_premium_preorder"] == "yes"
