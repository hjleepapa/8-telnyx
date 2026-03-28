"""Table-size allocation (greedy + bounded backtracking) for multi-slot inventory."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any


def floor_slot_start(dt: datetime, step_minutes: int) -> datetime:
    """Align `dt` to the start of a fixed grid (e.g. 30-minute buckets) in UTC."""
    dt = dt.astimezone(UTC)
    day_start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_min = int((dt - day_start).total_seconds() // 60)
    bucket = (elapsed_min // step_minutes) * step_minutes
    return day_start + timedelta(minutes=bucket)


def iter_occupied_slots(
    start: datetime,
    duration_minutes: int,
    step_minutes: int,
) -> list[datetime]:
    """All grid slots [t0, t1, …) touched by [start, start + duration)."""
    t0 = floor_slot_start(start, step_minutes)
    n_slots = max(1, (duration_minutes + step_minutes - 1) // step_minutes)
    return [t0 + timedelta(minutes=i * step_minutes) for i in range(n_slots)]


def effective_counts_across_slots(slot_maps: list[dict[int, int]]) -> dict[int, int]:
    """Per size, min available across slots (same physical tables must exist every slot)."""
    if not slot_maps:
        return {}
    sizes: set[int] = set()
    for m in slot_maps:
        sizes.update(m.keys())
    return {s: min(m.get(s, 0) for m in slot_maps) for s in sizes}


def allocate_tables(
    party_size: int,
    counts: dict[int, int],
    *,
    max_tables: int = 2,
) -> list[int] | None:
    """
    Choose table sizes (with replacement multiplicities) so sum(s) >= party_size.
    Prefer one table; then combine up to max_tables. Mutates `counts` copy internally.
    """
    if party_size < 1 or max_tables < 1:
        return None
    sizes_sorted = sorted(counts.keys())
    work = {k: int(v) for k, v in counts.items() if v > 0}

    # Single table: smallest that fits
    for sz in sizes_sorted:
        if sz >= party_size and work.get(sz, 0) > 0:
            return [sz]

    # Combinations — backtrack on a copy
    w = work.copy()

    def backtrack(need: int, path: list[int], start_i: int) -> list[int] | None:
        if need <= 0 and path:
            return path
        if len(path) >= max_tables:
            return None
        for i in range(start_i, len(sizes_sorted)):
            sz = sizes_sorted[i]
            if w.get(sz, 0) <= 0:
                continue
            w[sz] -= 1
            res = backtrack(need - sz, path + [sz], i)
            if res:
                return res
            w[sz] += 1
        return None

    return backtrack(party_size, [], 0)


def multiset_subtract(slots_counts: list[dict[int, int]], allocation: list[int]) -> bool:
    """Return True if every slot map can satisfy `allocation` counts; does not mutate."""
    need = Counter(allocation)
    for m in slots_counts:
        for sz, n in need.items():
            if m.get(sz, 0) < n:
                return False
    return True


def summarize_inventory_for_log(maps: list[dict[int, Any]]) -> str:
    if not maps:
        return "{}"
    eff = effective_counts_across_slots(maps)
    return str(dict(sorted(eff.items())))
