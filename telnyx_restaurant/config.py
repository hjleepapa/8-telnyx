"""Load settings from environment. Never commit secrets."""

from __future__ import annotations

import os

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


def hanok_reservation_lab_enabled() -> bool:
    """If true, serve GET /reservation-lab (browser helper for API scenarios). Use with ADMIN_DASHBOARD_TOKEN on public hosts."""
    return (os.environ.get("HANOK_RESERVATION_LAB") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
