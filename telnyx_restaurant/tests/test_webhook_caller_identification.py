"""Caller-line enrichment on dynamic webhook variables."""

from __future__ import annotations

import pytest

from telnyx_restaurant.routers.webhook import _enrich_caller_identification_for_profile


def test_enrich_no_caller_skips_db_hint() -> None:
    p: dict = {}
    _enrich_caller_identification_for_profile(p, None)
    assert p["caller_phone_normalized"] == ""
    assert p["caller_line_has_multiple_bookings"] == "no"
    assert p["caller_line_single_booking"] == "no"
    assert p["caller_line_reservation_count"] == "0"
    assert p["guest_personalized_greeting_suggestion"] == ""
    assert p["guest_lookup_name_for_tools"] == ""
    assert "telnyx_end_user_target" in p["guest_lookup_identification_hint"].lower()


def test_enrich_normalizes_us_ani(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_URI", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p: dict = {}
    _enrich_caller_identification_for_profile(p, "+1 (415) 555-0100")
    assert p["caller_phone_telnyx"] == "+1 (415) 555-0100"
    assert p["caller_phone_normalized"] == "+14155550100"
