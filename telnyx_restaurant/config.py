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
