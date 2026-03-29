"""Interpret reservation ``starts_at`` for storage (UTC): naive times = restaurant wall clock."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def interpret_starts_at_as_utc_storage(dt: datetime, wall_tz: ZoneInfo) -> datetime:
    """If ``dt`` is naive, treat year/month/day/hour/minute as *local* in ``wall_tz``; return aware UTC.

    If ``dt`` already has ``tzinfo``, convert to UTC (unchanged meaning for explicit Z / offsets).
    Voice tools often send ``2026-03-30T18:00:00`` with no zone — that must not mean 18:00 UTC.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    return dt.replace(tzinfo=wall_tz).astimezone(UTC)
