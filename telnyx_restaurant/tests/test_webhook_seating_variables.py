"""Seating / waitlist fields on dynamic webhook variables."""

from __future__ import annotations

from telnyx_restaurant.routers.webhook import _seating_waitlist_profile


def test_allocation_disabled_short_circuits(monkeypatch) -> None:
    monkeypatch.delenv("HANOK_TABLE_ALLOCATION_ENABLED", raising=False)
    v = _seating_waitlist_profile(
        food_total_cents=99_999,
        guest_priority_raw="vip",
        seating_status_raw="waitlist",
    )
    assert v["reservation_seating_status"] == "not_applicable"
    assert v["guest_waitlist_priority"] == "normal"
    assert "disabled" in v["waitlist_fairness_hint"].lower()


def test_waitlist_vip_by_preorder_size(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    monkeypatch.setenv("HANOK_VIP_PREORDER_CENTS", "50000")
    v = _seating_waitlist_profile(
        food_total_cents=50_000,
        guest_priority_raw="normal",
        seating_status_raw="waitlist",
    )
    assert v["guest_waitlist_priority"] == "vip"
    assert "before standard-priority" in v["waitlist_fairness_hint"]


def test_waitlist_standard_guest(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    monkeypatch.setenv("HANOK_VIP_PREORDER_CENTS", "50000")
    v = _seating_waitlist_profile(
        food_total_cents=1000,
        guest_priority_raw="normal",
        seating_status_raw="waitlist",
    )
    assert v["guest_waitlist_priority"] == "normal"
    assert "standard priority" in v["waitlist_fairness_hint"]


def test_waitlist_explicit_vip_flag(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _seating_waitlist_profile(
        food_total_cents=0,
        guest_priority_raw="vip",
        seating_status_raw="waitlist",
    )
    assert v["guest_waitlist_priority"] == "vip"


def test_allocated_table_hint(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _seating_waitlist_profile(
        food_total_cents=0,
        guest_priority_raw="normal",
        seating_status_raw="allocated",
    )
    assert v["reservation_seating_status"] == "allocated"
    assert "not waitlisted" in v["waitlist_fairness_hint"].lower()
