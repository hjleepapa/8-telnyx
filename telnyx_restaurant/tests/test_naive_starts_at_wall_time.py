"""Naive starts_at is interpreted as restaurant wall time, not UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from telnyx_restaurant.datetime_wall import interpret_starts_at_as_utc_storage


def test_naive_six_pm_march_30_2026_is_evening_pacific() -> None:
    la = ZoneInfo("America/Los_Angeles")
    naive = datetime(2026, 3, 30, 18, 0)
    utc = interpret_starts_at_as_utc_storage(naive, la)
    back = utc.astimezone(la)
    assert back.hour == 18
    assert back.minute == 0
    assert back.date().isoformat() == "2026-03-30"


def test_explicit_utc_unchanged_meaning() -> None:
    la = ZoneInfo("America/Los_Angeles")
    aware_utc = datetime(2026, 3, 30, 18, 0, tzinfo=UTC)
    out = interpret_starts_at_as_utc_storage(aware_utc, la)
    assert out == aware_utc
    assert out.astimezone(la).hour == 11
    assert out.astimezone(la).minute == 0
