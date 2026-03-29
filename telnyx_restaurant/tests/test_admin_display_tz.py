"""Admin calendar displays reservation wall time in configured display timezone."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.routers import admin as admin_router


def test_starts_at_display_uses_la_wall_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HANOK_ADMIN_DISPLAY_TIMEZONE", "America/Los_Angeles")
    r = Reservation(
        confirmation_code="HNK-TZ1",
        guest_name="Test",
        guest_phone="+15550001111",
        party_size=2,
        starts_at=datetime(2026, 7, 15, 2, 30, tzinfo=UTC),
        status=ReservationStatus.confirmed.value,
    )
    text = admin_router._starts_at_display_local(r)
    assert "your time)" in text
    assert "2026-07-14" in text
    assert "07:30 PM" in text or "7:30 PM" in text
    assert "PDT" in text or "PST" in text
