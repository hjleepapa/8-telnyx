"""Load settings from environment. Never commit secrets."""

from __future__ import annotations

import os
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def database_url() -> str | None:
    """Render/Postgres URL from DB_URI or DATABASE_URL (SQLAlchemy + psycopg2)."""
    raw = (os.environ.get("DB_URI") or os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        return None
    url = raw
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "postgresql+psycopg2://" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if "sslmode=" not in url and "render.com" in url:
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"
    return url


def admin_dashboard_token() -> str | None:
    """If set, GET /admin/reservations requires ?token=..."""
    return os.environ.get("ADMIN_DASHBOARD_TOKEN") or None


def telnyx_api_key() -> str | None:
    """Bearer token for Telnyx REST (outbound reminder demo)."""
    v = (os.environ.get("TELNYX_API_KEY") or os.environ.get("TELNYX_API_TOKEN") or "").strip()
    return v or None


def telnyx_connection_id() -> str | None:
    """Call Control App ID / connection UUID for `POST /v2/calls`."""
    v = (os.environ.get("TELNYX_CONNECTION_ID") or "").strip()
    return v or None


def telnyx_from_number() -> str | None:
    """Verified or permitted caller ID (+E.164) for outbound calls."""
    v = (os.environ.get("TELNYX_FROM_NUMBER") or "").strip()
    return v or None


def hanok_reminder_delay_seconds() -> float:
    """Outbound demo reminder delay after reservation is saved (default 5s; max 5 minutes)."""
    raw = (os.environ.get("HANOK_REMINDER_DELAY_SECONDS") or "5").strip()
    try:
        return max(1.0, min(float(raw), 300.0))
    except ValueError:
        return 5.0


def hanok_reservation_wall_clock_timezone():
    """IANA zone for naive ``starts_at`` (voice/MCP strings without offset = this wall clock).

    Prefer ``HANOK_RESERVATION_WALL_TIMEZONE``; else ``HANOK_ADMIN_DISPLAY_TIMEZONE``;
    default ``America/Los_Angeles``.
    """
    from zoneinfo import ZoneInfo

    raw = (
        (os.environ.get("HANOK_RESERVATION_WALL_TIMEZONE") or "").strip()
        or (os.environ.get("HANOK_ADMIN_DISPLAY_TIMEZONE") or "").strip()
        or "America/Los_Angeles"
    )
    return ZoneInfo(raw)


def hanok_public_base_url() -> str | None:
    """Public HTTPS origin for Telnyx webhooks (no trailing slash), e.g. https://telnyx.convonetai.com."""
    v = (
        os.environ.get("HANOK_PUBLIC_BASE_URL")
        or os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or ""
    ).strip()
    if not v:
        return None
    return v.rstrip("/")


def hanok_mcp_api_base_url() -> str:
    """Origin the MCP server calls for REST (no trailing slash).

    Prefer HANOK_MCP_API_BASE_URL when the FastAPI app is not on the same host as MCP
    (e.g. MCP on laptop, API on Render). Otherwise HANOK_PUBLIC_BASE_URL; local fallback 127.0.0.1:8000.
    """
    v = (os.environ.get("HANOK_MCP_API_BASE_URL") or "").strip().rstrip("/")
    if v:
        return v
    pub = hanok_public_base_url()
    if pub:
        return pub
    return "http://127.0.0.1:8000"


def hanok_mcp_streamable_transport_security():
    """DNS rebinding settings for MCP streamable HTTP (Host / Origin checks).

    Uses ``HANOK_PUBLIC_BASE_URL`` / ``HANOK_MCP_API_BASE_URL`` hostnames when set.
    Set ``HANOK_MCP_DISABLE_DNS_REBINDING=1`` to turn checks off (not recommended on untrusted networks).
    """
    from mcp.server.transport_security import TransportSecuritySettings

    if (os.environ.get("HANOK_MCP_DISABLE_DNS_REBINDING") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    hosts: list[str] = []
    origins: list[str] = []

    def add_public_origin(origin: str) -> None:
        o = origin.strip().rstrip("/")
        if not o:
            return
        p = urlparse(o)
        host = (p.hostname or "").lower()
        if not host or host in ("127.0.0.1", "localhost", "::1"):
            return
        hosts.append(host)
        hosts.append(f"{host}:*")
        if p.scheme and p.netloc:
            origins.append(f"{p.scheme}://{p.netloc}")

    pub = hanok_public_base_url()
    if pub:
        add_public_origin(pub)
    mcp_api = (os.environ.get("HANOK_MCP_API_BASE_URL") or "").strip().rstrip("/")
    if mcp_api:
        add_public_origin(mcp_api)

    for part in (os.environ.get("HANOK_MCP_ALLOWED_HOSTS") or "").split(","):
        h = part.strip()
        if h:
            hosts.append(h)

    for part in (os.environ.get("HANOK_MCP_ALLOWED_ORIGINS") or "").split(","):
        o = part.strip().rstrip("/")
        if o:
            origins.append(o)

    if not hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


def hanok_mcp_http_mount_enabled() -> bool:
    """If true, mount FastMCP streamable HTTP on FastAPI at ``hanok_mcp_http_mount_path()`` (one uvicorn — Telnyx HTTP MCP URL)."""
    return (os.environ.get("HANOK_MCP_HTTP_MOUNT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def hanok_mcp_http_mount_path() -> str:
    """URL prefix for the mounted MCP app (no trailing slash)."""
    p = (os.environ.get("HANOK_MCP_HTTP_MOUNT_PATH") or "/mcp").strip()
    if not p.startswith("/"):
        p = "/" + p
    p = p.rstrip("/")
    return p if p else "/mcp"


def hanok_voice_create_dedup_seconds() -> int:
    """Voice POST /api/reservations: treat duplicate creates within this window as one row (0 = off). Default 120s."""
    raw = (os.environ.get("HANOK_VOICE_CREATE_DEDUP_SECONDS") or "120").strip()
    try:
        return max(0, min(int(float(raw)), 3600))
    except ValueError:
        return 120


def hanok_premium_preorder_cents_threshold() -> int:
    """``food_total_cents`` at or above this marks high-value pre-orders (dynamic webhook variables). Default 50000 ($500)."""
    raw = (os.environ.get("HANOK_PREMIUM_PREORDER_CENTS") or "50000").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 50000


def hanok_reservation_verbose_logging() -> bool:
    """If true, log PATCH /amend and …/status bodies (truncated) at INFO for debugging Telnyx tools."""
    return (os.environ.get("HANOK_RESERVATION_VERBOSE_LOG") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def hanok_table_allocation_enabled() -> bool:
    """Per-slot table inventory + allocation on create; cancel releases and promotes waitlist."""
    return (os.environ.get("HANOK_TABLE_ALLOCATION_ENABLED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def hanok_slot_step_minutes() -> int:
    try:
        v = int((os.environ.get("HANOK_TABLE_SLOT_MINUTES") or "30").strip())
        return max(15, min(v, 120))
    except ValueError:
        return 30


def hanok_default_reservation_duration_minutes() -> int:
    try:
        v = int((os.environ.get("HANOK_RESERVATION_DURATION_MINUTES") or "120").strip())
        return max(30, min(v, 480))
    except ValueError:
        return 120


def hanok_max_tables_per_party() -> int:
    try:
        v = int((os.environ.get("HANOK_MAX_TABLES_PER_PARTY") or "2").strip())
        return max(1, min(v, 4))
    except ValueError:
        return 2


def hanok_table_inventory_template() -> dict[int, int]:
    """Default counts per table size when a new (slot_start, size) inventory row is created."""
    import json

    raw = (os.environ.get("HANOK_TABLE_INVENTORY_JSON") or "").strip()
    if not raw:
        return {4: 2, 6: 3, 8: 3, 10: 2}
    try:
        data = json.loads(raw)
        return {int(k): int(v) for k, v in data.items()}
    except (json.JSONDecodeError, ValueError, TypeError):
        return {4: 2, 6: 3, 8: 3, 10: 2}


def hanok_vip_preorder_threshold_cents() -> int:
    """Preorder total at or above this (cents) → guest_priority VIP for waitlist ordering."""
    try:
        return int((os.environ.get("HANOK_VIP_PREORDER_CENTS") or "50000").strip())
    except ValueError:
        return 50000


def hanok_waitlist_minutes_per_position() -> int:
    """For dynamic variables: estimated wait ≈ position × this many minutes (default 15)."""
    try:
        v = int((os.environ.get("HANOK_WAITLIST_MINUTES_PER_POSITION") or "15").strip())
        return max(5, min(v, 120))
    except ValueError:
        return 15


def hanok_waitlist_max_per_slot() -> int:
    """Max **weighted** waitlist occupancy per seating window (floored slot + duration).

    Each waitlisted party contributes ``N`` units, where ``N`` is how many tables it needs against
    a full template (usually 1; large parties can be 2). The sum of units in the window must stay
    **at or below** this value; otherwise new waitlist joins return HTTP 409 / ``SeatingUnavailableError``.
    """
    try:
        v = int((os.environ.get("HANOK_WAITLIST_MAX_PER_SLOT") or "5").strip())
        return max(1, min(v, 50))
    except ValueError:
        return 5


def hanok_reservation_lab_enabled() -> bool:
    """If true, serve GET /reservation-lab (browser helper for API scenarios). Use with ADMIN_DASHBOARD_TOKEN on public hosts."""
    return (os.environ.get("HANOK_RESERVATION_LAB") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
