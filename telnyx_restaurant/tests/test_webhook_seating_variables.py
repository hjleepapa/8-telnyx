"""Seating / waitlist fields on dynamic webhook variables."""

from __future__ import annotations

from datetime import UTC, datetime

from telnyx_restaurant.routers.webhook import (
    _lifecycle_seating_voice_hints,
    _seating_waitlist_profile,
    _waitlist_queue_speech_variables,
)
from telnyx_restaurant.schemas_res import ReservationRead


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


def test_waitlist_queue_speech_variables_eta(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _waitlist_queue_speech_variables(
        queue_meta={"position": 2, "queue_size": 5, "estimated_wait_minutes": 30},
        seating_status_resolved="waitlist",
    )
    assert v["guest_waitlist_position"] == "2"
    assert v["guest_waitlist_queue_size"] == "5"
    assert v["guest_waitlist_estimated_wait_minutes"] == "30"
    assert v["guest_waitlist_position_ordinal_en"] == "second"
    assert "30" in v["guest_waitlist_wait_time_hint"]
    assert v["guest_waitlist_can_seat_after_ahead"] == "yes"
    assert v["guest_waitlist_tables_required"] == "0"


def test_waitlist_queue_speech_infeasible_multi_table(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _waitlist_queue_speech_variables(
        queue_meta={
            "position": 4,
            "queue_size": 5,
            "estimated_wait_minutes": 60,
            "tables_required": 2,
            "feasible_after_ahead": False,
            "ahead_chain_ok": True,
        },
        seating_status_resolved="waitlist",
    )
    assert v["guest_waitlist_can_seat_after_ahead"] == "no"
    assert v["guest_waitlist_tables_required"] == "2"
    assert "two hours" in v["guest_waitlist_seating_capacity_hint"].lower()
    assert "two hours" in v["guest_waitlist_wait_time_hint"].lower()


def test_waitlist_queue_speech_not_on_list(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _waitlist_queue_speech_variables(queue_meta=None, seating_status_resolved="allocated")
    assert v["guest_waitlist_position"] == "0"
    assert v["guest_waitlist_estimated_wait_minutes"] == "0"


def test_waitlist_queue_speech_unknown_when_waitlist_but_meta_missing(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    monkeypatch.setenv("HANOK_WAITLIST_MINUTES_PER_POSITION", "15")
    v = _waitlist_queue_speech_variables(queue_meta=None, seating_status_resolved="waitlist")
    assert v["guest_waitlist_position"] == "unknown"
    assert v["guest_waitlist_estimated_wait_minutes"] == "unknown"
    assert "waitlist" in v["guest_waitlist_wait_time_hint"].lower()
    assert "15" in v["guest_waitlist_wait_time_hint"]
    assert "do not guess" in v["guest_waitlist_wait_time_hint"].lower()


def test_lifecycle_hint_confirmed_waitlist_opens_with_waitlist() -> None:
    h = _lifecycle_seating_voice_hints(lifecycle_status="confirmed", seating_status="waitlist")
    assert h["reservation_status_means_table_secured"] == "no"
    assert "WAITLIST" in h["reservation_opening_speech_hint"]


def test_lifecycle_hint_confirmed_allocated_table_secured() -> None:
    h = _lifecycle_seating_voice_hints(lifecycle_status="confirmed", seating_status="allocated")
    assert h["reservation_status_means_table_secured"] == "yes"


def test_reservation_read_assistant_hint_confirmed_waitlist() -> None:
    now = datetime.now(UTC)
    r = ReservationRead(
        id=60,
        confirmation_code="HNK-2G2A",
        guest_name="James",
        guest_phone="+19259897841",
        party_size=4,
        starts_at=now,
        status="confirmed",
        special_requests=None,
        seating_status="waitlist",
        created_at=now,
        updated_at=now,
    )
    d = r.model_dump(mode="python")
    assert "assistant_seating_opening_hint" in d
    assert "WAITLIST" in d["assistant_seating_opening_hint"]


def test_allocated_table_hint(monkeypatch) -> None:
    monkeypatch.setenv("HANOK_TABLE_ALLOCATION_ENABLED", "1")
    v = _seating_waitlist_profile(
        food_total_cents=0,
        guest_priority_raw="normal",
        seating_status_raw="allocated",
    )
    assert v["reservation_seating_status"] == "allocated"
    assert "not waitlisted" in v["waitlist_fairness_hint"].lower()
