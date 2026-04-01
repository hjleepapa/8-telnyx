"""MCP tool surface for Hanok Table — wraps the existing FastAPI reservation API."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from telnyx_restaurant.config import hanok_mcp_api_base_url, hanok_mcp_streamable_transport_security

_INSTRUCTIONS = (
    "Hanok Table reservation API tools. The Telnyx dynamic-variables webhook may provide "
    "locale_hint (en-US or ko-KR) from the guest's stored preferred_locale — follow that language "
    "for the conversation when set. When the webhook exposes caller_phone_normalized, pass it as guest_phone on "
    "get_reservation. If caller_line_single_booking is yes, call get_reservation with ONLY guest_phone "
    "(omit guest_name / leave it empty)—this uses phone-only API lookup; greet first using "
    "guest_personalized_greeting_suggestion and do NOT ask for their name before that tool call. "
    "If caller_line_has_multiple_bookings is yes, ask which name the booking is under and pass guest_name. "
    "Always call get_reservation (lookup) before "
    "update_reservation_details or set_reservation_status so you have the numeric "
    "reservation id. Use list_menu_items before building preorder lines. "
    "When the guest orders food, pass preorder on create/update: either preorder_items "
    "(e.g. bulgogi:2,kimchi_jjigae:1) or preorder_lines_json — do not submit create_reservation "
    "with no preorder if they chose dishes. "
    "Details patch: PATCH /{id}/amend; lifecycle/cancel: PATCH /{id}/status. "
    "Cancelling: the API may return HTTP 409 premium_cancel_requires_retention_step for large pre-orders ($300+ "
    "food total by default). Read the error message, speak the offer from cancel_retention_offer, then call "
    "cancel_reservation or set_reservation_status again with retention_offer_acknowledged=true (or PATCH JSON "
    "{\"status\":\"cancelled\",\"retention_offer_acknowledged\":true}). "
    "After cancel_reservation or set_reservation_status succeeds, say a brief spoken "
    "confirmation (e.g. reservation cancelled, code HNK-…) — do not stay silent until the user speaks. "
    "Reservation JSON has both status (lifecycle, often confirmed) and seating_status. "
    "If seating_status is waitlist, the guest has no table yet—say waitlist first; never treat status=confirmed alone "
    "as meaning a table is secured. Use guest_waitlist_* / wait_time_hint from webhook variables when present; "
    "read assistant_seating_opening_hint on API responses."
)

mcp = FastMCP(
    "hanok-table-reservations",
    instructions=_INSTRUCTIONS,
    json_response=True,
    stateless_http=True,
    transport_security=hanok_mcp_streamable_transport_security(),
)


def _http_timeout() -> httpx.Timeout:
    raw = (os.environ.get("HANOK_MCP_HTTP_TIMEOUT_SECONDS") or "45").strip()
    try:
        sec = max(5.0, min(float(raw), 120.0))
    except ValueError:
        sec = 45.0
    return httpx.Timeout(sec)


def _preorder_lines_from_simple(spec: str) -> list[dict[str, Any]]:
    """Parse ``bulgogi:2, bibimbap:1`` or ``2x bulgogi`` into API preorder lines (menu ids from catalog)."""
    from telnyx_restaurant.menu_catalog import resolve_menu_item_id

    lines: list[dict[str, Any]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        qty = 1
        dish_part = part
        m = re.match(r"^(\d+)\s*x\s*(.+)$", part, re.I)
        if m:
            qty = max(1, int(m.group(1)))
            dish_part = m.group(2).strip()
        elif ":" in part:
            left, _, right = part.partition(":")
            left, right = left.strip(), right.strip()
            if right.isdigit():
                dish_part = left
                qty = max(1, int(right))
        mid = resolve_menu_item_id(dish_part.strip(), None)
        lines.append({"menu_item_id": mid, "quantity": qty})
    if not lines:
        raise ValueError(
            "preorder_items had no lines — use ids from list_menu_items (e.g. bulgogi:2,dolsot_bibimbap:1)"
        )
    return lines


def _preorder_for_api_body(
    preorder_lines_json: str | None,
    preorder_items: str | None,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Build preorder list for JSON body; return (list | None, error_response_body | None)."""
    if preorder_lines_json and preorder_lines_json.strip():
        try:
            data = json.loads(preorder_lines_json)
        except json.JSONDecodeError as e:
            return None, json.dumps({"error": "invalid_preorder_json", "detail": str(e)}, indent=2)
        if isinstance(data, list):
            return (data if data else None), None
        if isinstance(data, dict):
            return [data], None
        return None, json.dumps(
            {"error": "invalid_preorder_json", "detail": "JSON must be an array or object"},
            indent=2,
        )
    if preorder_items and preorder_items.strip():
        try:
            return _preorder_lines_from_simple(preorder_items.strip()), None
        except ValueError as e:
            return None, json.dumps({"error": "invalid_preorder_items", "detail": str(e)}, indent=2)
    return None, None


def _fmt_response(status_code: int, text: str) -> str:
    try:
        data = json.loads(text) if text.strip() else {}
        return json.dumps({"http_status": status_code, "data": data}, indent=2, default=str)
    except json.JSONDecodeError:
        return json.dumps(
            {"http_status": status_code, "raw_body": text[:8000]},
            indent=2,
        )


async def _http_json(method: str, path: str, **kwargs: Any) -> str:
    """Async HTTP so the event loop stays free when MCP is mounted in the same uvicorn as the API.

    ``asyncio.to_thread`` + blocking ``httpx`` to the same process exhausted the default thread pool:
    every worker thread blocked on a response while the loop could not run route handlers (60s timeouts).
    """
    try:
        async with httpx.AsyncClient(base_url=hanok_mcp_api_base_url(), timeout=_http_timeout()) as client:
            r = await client.request(method, path, **kwargs)
            return _fmt_response(r.status_code, r.text)
    except httpx.RequestError as e:
        return json.dumps(
            {
                "error": "request_failed",
                "detail": str(e),
                "hint": f"Set HANOK_MCP_API_BASE_URL or HANOK_PUBLIC_BASE_URL (current base: {hanok_mcp_api_base_url()!r})",
            },
            indent=2,
        )


@mcp.tool()
async def list_menu_items() -> str:
    """List menu item ids, English names, and price_cents for pre-order lines."""
    return await _http_json("GET", "/api/reservations/menu/items")


@mcp.tool()
async def get_reservation(guest_phone: str, guest_name: str | None = None) -> str:
    """Look up the active reservation. Pass guest_phone from webhook caller_phone_normalized (or ANI).

    When caller_line_single_booking is yes, pass **only guest_phone**—omit guest_name—so lookup runs without
    asking the caller for their name first (uses GET /lookup-by-phone). When multiple bookings share the line,
    pass guest_name after disambiguation (uses GET /lookup).
    """
    gp = (guest_phone or "").strip()
    gn = (guest_name or "").strip()
    if not gp:
        return json.dumps(
            {
                "http_status": 422,
                "data": {"detail": "guest_phone is required (use caller_phone_normalized from webhook)."},
            },
            indent=2,
        )
    if gn:
        return await _http_json(
            "GET",
            "/api/reservations/lookup",
            params={"guest_name": gn, "guest_phone": gp},
        )
    return await _http_json(
        "GET",
        "/api/reservations/lookup-by-phone",
        params={"guest_phone": gp},
    )


@mcp.tool()
async def get_reservation_by_code(confirmation_code: str) -> str:
    """Fetch one reservation by HNK confirmation code (e.g. HNK-AB12)."""
    code = confirmation_code.strip().upper()
    if not code.startswith("HNK-"):
        code = f"HNK-{code.removeprefix('HNK-').removeprefix('hnk-')}"
    return await _http_json("GET", f"/api/reservations/by-code/{code}")


@mcp.tool()
async def search_seating_availability(date: str) -> str:
    """
    Per-slot table availability for a UTC calendar day (YYYY-MM-DD).
    Requires HANOK_TABLE_ALLOCATION_ENABLED on the API host; otherwise returns 404 with explanation.
    """
    return await _http_json("GET", "/api/reservations/seating/availability", params={"date": date.strip()})


@mcp.tool()
async def create_reservation(
    guest_name: str,
    guest_phone: str,
    party_size: int,
    starts_at: str,
    preorder_lines_json: str | None = None,
    preorder_items: str | None = None,
    special_requests: str | None = None,
    preferred_locale: str | None = None,
    source_channel: str = "voice",
) -> str:
    """
    Create a reservation. starts_at must be ISO-8601.
    Prefer an explicit offset (e.g. 2026-03-30T18:00:00-07:00 for 6 PM Pacific).
    If you omit the zone (2026-03-30T18:00:00), the API treats that clock time as the
    restaurant's local timezone (default America/Los_Angeles), not UTC.

    Pre-order (if the guest ordered food — call list_menu_items first):
    - preorder_items: easiest for voice — comma-separated id:quantity, e.g. ``bulgogi:2,kimchi_jjigae:1``
      or ``2x bulgogi, 1x dolsot_bibimbap`` (menu_item ids / aliases from list_menu_items).
    - preorder_lines_json: alternatively a JSON array, e.g. [{"menu_item_id":"bulgogi","quantity":2}]
    If both are set, preorder_lines_json wins.
    preferred_locale: optional ``en`` or ``ko`` (stored for dynamic webhook / future calls).

    Response may have seating_status waitlist when no table fits—read assistant_seating_opening_hint and
    seating_status; do not tell the guest their table is confirmed when seating_status is waitlist.
    When waitlisted, JSON includes guest_waitlist_estimated_wait_minutes, guest_waitlist_position,
    guest_waitlist_queue_size, and guest_waitlist_wait_time_hint—read those aloud (approximate ETA).
    """
    body: dict[str, Any] = {
        "guest_name": _clean_str(guest_name),
        "guest_phone": _clean_str(guest_phone),
        "party_size": int(party_size),
        "starts_at": _clean_str(starts_at),
        "source_channel": (source_channel or "voice").strip().lower(),
    }
    if preferred_locale is not None and str(preferred_locale).strip():
        from telnyx_restaurant.locale_prefs import normalize_preferred_locale

        body["preferred_locale"] = normalize_preferred_locale(preferred_locale)
    if special_requests and special_requests.strip():
        body["special_requests"] = special_requests.strip()
    preorder, err_body = _preorder_for_api_body(preorder_lines_json, preorder_items)
    if err_body:
        return err_body
    if preorder:
        body["preorder"] = preorder
    return await _http_json(
        "POST",
        "/api/reservations",
        json=body,
        headers={"Content-Type": "application/json"},
    )


@mcp.tool()
async def update_reservation_details(
    reservation_id: int,
    party_size: int | None = None,
    starts_at: str | None = None,
    preorder_lines_json: str | None = None,
    preorder_items: str | None = None,
    special_requests: str | None = None,
    guest_name: str | None = None,
    guest_phone: str | None = None,
    preferred_locale: str | None = None,
    guest_priority: str | None = None,
) -> str:
    """
    Change food pre-order, party size, time, notes, guest contact, or waitlist tier.
    PATCHes /api/reservations/{id}/amend. Omit fields you do not change.
    guest_priority: normal or vip (VIP affects waitlist ordering when table allocation is on).
    preorder_lines_json: JSON array; preorder_items: e.g. bulgogi:2,kimchi_jjigae:1 (JSON wins if both set).
    """
    body: dict[str, Any] = {}
    if party_size is not None:
        body["party_size"] = int(party_size)
    if starts_at is not None and str(starts_at).strip():
        body["starts_at"] = str(starts_at).strip()
    if special_requests is not None:
        body["special_requests"] = special_requests if special_requests else None
    if guest_name is not None and str(guest_name).strip():
        body["guest_name"] = str(guest_name).strip()
    if guest_phone is not None and str(guest_phone).strip():
        body["guest_phone"] = str(guest_phone).strip()
    if preorder_lines_json is not None and str(preorder_lines_json).strip():
        try:
            body["preorder"] = json.loads(preorder_lines_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": "invalid_preorder_json", "detail": str(e)}, indent=2)
    elif preorder_items is not None and preorder_items.strip():
        try:
            body["preorder"] = _preorder_lines_from_simple(preorder_items.strip())
        except ValueError as e:
            return json.dumps({"error": "invalid_preorder_items", "detail": str(e)}, indent=2)
    if preferred_locale is not None and str(preferred_locale).strip():
        from telnyx_restaurant.locale_prefs import normalize_preferred_locale

        body["preferred_locale"] = normalize_preferred_locale(preferred_locale)
    if guest_priority is not None and str(guest_priority).strip():
        gp = str(guest_priority).strip().lower()
        body["guest_priority"] = "vip" if gp in ("vip", "v") else "normal"
    if not body:
        return json.dumps(
            {
                "error": "no_fields",
                "detail": "Provide at least one of party_size, starts_at, preorder_lines_json, preorder_items, special_requests, guest_name, guest_phone, preferred_locale, guest_priority",
            },
            indent=2,
        )
    return await _http_json(
        "PATCH",
        f"/api/reservations/{int(reservation_id)}/amend",
        json=body,
        headers={"Content-Type": "application/json"},
    )


async def _patch_reservation_status(
    reservation_id: int,
    status: str,
    *,
    retention_offer_acknowledged: bool = False,
) -> str:
    st = status.strip().lower()
    if st in ("cancel", "canceled", "cancellation"):
        st = "cancelled"
    body: dict[str, Any] = {"status": st}
    if st == "cancelled" and retention_offer_acknowledged:
        body["retention_offer_acknowledged"] = True
    return await _http_json(
        "PATCH",
        f"/api/reservations/{int(reservation_id)}/status",
        json=body,
        headers={"Content-Type": "application/json"},
    )


@mcp.tool()
async def set_reservation_status(
    reservation_id: int,
    status: str,
    retention_offer_acknowledged: bool = False,
) -> str:
    """
    Update lifecycle only: pending, confirmed, seated, completed, cancelled.
    For cancel you may pass cancelled, cancel, or canceled.
    If the API returned 409 premium_cancel_requires_retention_step, set retention_offer_acknowledged=true on retry.
    """
    return await _patch_reservation_status(
        reservation_id,
        status,
        retention_offer_acknowledged=retention_offer_acknowledged,
    )


@mcp.tool()
async def cancel_reservation(
    reservation_id: int,
    retention_offer_acknowledged: bool = False,
) -> str:
    """Cancel a reservation (sets status to cancelled).

    After a 409 premium retention response, call again with retention_offer_acknowledged=true.
    """
    return await _patch_reservation_status(
        reservation_id,
        "cancelled",
        retention_offer_acknowledged=retention_offer_acknowledged,
    )


def _clean_str(s: str) -> str:
    return (s or "").strip()


@mcp.resource("hanok://api-base")
def resource_api_base() -> str:
    """Configured REST origin this MCP server calls."""
    return json.dumps(
        {"HANOK_MCP_API_BASE_URL_or_public": hanok_mcp_api_base_url()},
        indent=2,
    )


@mcp.prompt()
def reservation_voice_flow() -> str:
    """Suggested turn flow for Telnyx voice booking."""
    return (
        "For phone booking: (1) Greet using webhook variables if available. "
        "(2) For lookup/modify/cancel: if caller_line_single_booking is yes, call get_reservation with ONLY "
        "guest_phone (= caller_phone_normalized), no guest_name—greet first, do not ask name. "
        "If multiple bookings on the line, ask name then pass guest_name. "
        "(3) Use list_menu_items before preorder. "
        "(4) create_reservation for new bookings (source_channel voice). "
        "(5) update_reservation_details for food/time/party/notes; set_reservation_status or cancel_reservation for lifecycle. "
        "(6) Confirm code and time aloud."
    )


def main() -> None:
    transport = (os.environ.get("HANOK_MCP_TRANSPORT") or "stdio").strip().lower()
    if transport not in ("stdio", "sse", "streamable-http"):
        transport = "stdio"
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
