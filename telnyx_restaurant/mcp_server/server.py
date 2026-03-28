"""MCP tool surface for Hanok Table — wraps the existing FastAPI reservation API."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from telnyx_restaurant.config import hanok_mcp_api_base_url, hanok_mcp_streamable_transport_security

_INSTRUCTIONS = (
    "Hanok Table reservation API tools. Always call get_reservation (lookup) before "
    "update_reservation_details or set_reservation_status so you have the numeric "
    "reservation id. Use list_menu_items before building preorder lines. "
    "Details patch: PATCH /{id}/amend; lifecycle/cancel: PATCH /{id}/status."
)

mcp = FastMCP(
    "hanok-table-reservations",
    instructions=_INSTRUCTIONS,
    json_response=True,
    stateless_http=True,
    transport_security=hanok_mcp_streamable_transport_security(),
)


def _client() -> httpx.Client:
    return httpx.Client(base_url=hanok_mcp_api_base_url(), timeout=60.0)


def _fmt_response(status_code: int, text: str) -> str:
    try:
        data = json.loads(text) if text.strip() else {}
        return json.dumps({"http_status": status_code, "data": data}, indent=2, default=str)
    except json.JSONDecodeError:
        return json.dumps(
            {"http_status": status_code, "raw_body": text[:8000]},
            indent=2,
        )


def _http_json(method: str, path: str, **kwargs: Any) -> str:
    try:
        with _client() as client:
            r = client.request(method, path, **kwargs)
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
def list_menu_items() -> str:
    """List menu item ids, English names, and price_cents for pre-order lines."""
    return _http_json("GET", "/api/reservations/menu/items")


@mcp.tool()
def get_reservation(guest_name: str, guest_phone: str) -> str:
    """Look up the active reservation for this caller. guest_name is required; use E.164 phone (+1…)."""
    params = {"guest_name": guest_name.strip(), "guest_phone": guest_phone.strip()}
    return _http_json("GET", "/api/reservations/lookup", params=params)


@mcp.tool()
def get_reservation_by_code(confirmation_code: str) -> str:
    """Fetch one reservation by HNK confirmation code (e.g. HNK-AB12)."""
    code = confirmation_code.strip().upper()
    if not code.startswith("HNK-"):
        code = f"HNK-{code.removeprefix('HNK-').removeprefix('hnk-')}"
    return _http_json("GET", f"/api/reservations/by-code/{code}")


@mcp.tool()
def search_seating_availability(date: str) -> str:
    """
    Per-slot table availability for a UTC calendar day (YYYY-MM-DD).
    Requires HANOK_TABLE_ALLOCATION_ENABLED on the API host; otherwise returns 404 with explanation.
    """
    return _http_json("GET", "/api/reservations/seating/availability", params={"date": date.strip()})


@mcp.tool()
def create_reservation(
    guest_name: str,
    guest_phone: str,
    party_size: int,
    starts_at: str,
    preorder_lines_json: str | None = None,
    special_requests: str | None = None,
    source_channel: str = "voice",
) -> str:
    """
    Create a reservation. starts_at must be ISO-8601 (e.g. 2026-07-04T18:00:00+00:00).
    preorder_lines_json: optional JSON array, e.g. [{"menu_item_id":"bulgogi","quantity":2}]
    """
    body: dict[str, Any] = {
        "guest_name": _clean_str(guest_name),
        "guest_phone": _clean_str(guest_phone),
        "party_size": int(party_size),
        "starts_at": _clean_str(starts_at),
        "source_channel": (source_channel or "voice").strip().lower(),
    }
    if special_requests and special_requests.strip():
        body["special_requests"] = special_requests.strip()
    if preorder_lines_json and preorder_lines_json.strip():
        try:
            body["preorder"] = json.loads(preorder_lines_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": "invalid_preorder_json", "detail": str(e)}, indent=2)
    return _http_json(
        "POST",
        "/api/reservations",
        json=body,
        headers={"Content-Type": "application/json"},
    )


@mcp.tool()
def update_reservation_details(
    reservation_id: int,
    party_size: int | None = None,
    starts_at: str | None = None,
    preorder_lines_json: str | None = None,
    special_requests: str | None = None,
    guest_name: str | None = None,
    guest_phone: str | None = None,
) -> str:
    """
    Change food pre-order, party size, time, notes, or guest contact.
    PATCHes /api/reservations/{id}/amend. Omit fields you do not change.
    preorder_lines_json: JSON array or null to skip; use "[]" only if the API treats empty as no-op.
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
    if preorder_lines_json is not None and preorder_lines_json.strip():
        try:
            body["preorder"] = json.loads(preorder_lines_json)
        except json.JSONDecodeError as e:
            return json.dumps({"error": "invalid_preorder_json", "detail": str(e)}, indent=2)
    if not body:
        return json.dumps(
            {
                "error": "no_fields",
                "detail": "Provide at least one of party_size, starts_at, preorder_lines_json, special_requests, guest_name, guest_phone",
            },
            indent=2,
        )
    return _http_json(
        "PATCH",
        f"/api/reservations/{int(reservation_id)}/amend",
        json=body,
        headers={"Content-Type": "application/json"},
    )


def _patch_reservation_status(reservation_id: int, status: str) -> str:
    st = status.strip().lower()
    if st in ("cancel", "canceled", "cancellation"):
        st = "cancelled"
    body = {"status": st}
    return _http_json(
        "PATCH",
        f"/api/reservations/{int(reservation_id)}/status",
        json=body,
        headers={"Content-Type": "application/json"},
    )


@mcp.tool()
def set_reservation_status(reservation_id: int, status: str) -> str:
    """
    Update lifecycle only: pending, confirmed, seated, completed, cancelled.
    For cancel you may pass cancelled, cancel, or canceled.
    """
    return _patch_reservation_status(reservation_id, status)


@mcp.tool()
def cancel_reservation(reservation_id: int) -> str:
    """Cancel a reservation (sets status to cancelled)."""
    return _patch_reservation_status(reservation_id, "cancelled")


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
        "(2) For lookup/modify/cancel, call get_reservation with name + E.164 phone. "
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
