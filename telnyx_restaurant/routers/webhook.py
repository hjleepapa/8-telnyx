"""Telnyx Dynamic Webhook Variables — return JSON for assistant templates.

Map keys to variables configured in Telnyx Portal. Accepts a generic JSON body;
use `caller_number` or `from` (Telnyx-style) for demo profile lookup.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

router = APIRouter()


def _demo_profile_for_caller(caller_number: str | None) -> dict[str, Any]:
    """Synthetic guests — replace with DB lookup keyed by ANI."""
    normalized = (caller_number or "").strip()
    if normalized.endswith("0001"):
        return {
            "guest_display_name": "Jordan",
            "vip_tier": "gold",
            "preferred_venue_slug": "harbor-bistro",
            "default_party_size": 4,
            "locale_hint": "en-US",
            "has_upcoming_reservation": True,
        }
    return {
        "guest_display_name": "Guest",
        "vip_tier": "standard",
        "preferred_venue_slug": "harbor-bistro",
        "default_party_size": 2,
        "locale_hint": "en-US",
        "has_upcoming_reservation": False,
    }


@router.post("/variables")
async def dynamic_webhook_variables(
    payload: dict[str, Any] | None = Body(default=None),
) -> dict[str, Any]:
    """Return personalization variables for the AI Assistant instruction templates."""
    data = payload or {}
    caller = data.get("caller_number") or data.get("from")
    if isinstance(caller, str):
        caller = caller.strip()
    else:
        caller = None

    profile = _demo_profile_for_caller(caller)
    profile["_demo_caller"] = caller or "unknown"
    return profile
