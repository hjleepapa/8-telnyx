"""Pure allocation / slot grid tests (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from telnyx_restaurant.table_allocation import (
    allocate_tables,
    effective_counts_across_slots,
    floor_slot_start,
    iter_occupied_slots,
    multiset_subtract,
)


def test_floor_slot_start_30m():
    dt = datetime(2026, 3, 28, 18, 14, tzinfo=UTC)
    assert floor_slot_start(dt, 30) == datetime(2026, 3, 28, 18, 0, tzinfo=UTC)


def test_iter_occupied_slots_duration():
    start = datetime(2026, 3, 28, 18, 0, tzinfo=UTC)
    slots = iter_occupied_slots(start, 120, 30)
    assert len(slots) == 4
    assert slots[0] == start
    assert slots[-1] == start + timedelta(minutes=90)


def test_effective_counts_min_across_slots():
    m1 = {4: 2, 6: 1}
    m2 = {4: 1, 6: 2}
    eff = effective_counts_across_slots([m1, m2])
    assert eff[4] == 1
    assert eff[6] == 1


def test_allocate_smallest_single():
    counts = {4: 2, 6: 3, 8: 1, 10: 2}
    assert allocate_tables(5, counts.copy(), max_tables=2) == [6]
    assert allocate_tables(4, counts.copy(), max_tables=2) == [4]


def test_allocate_combine_two():
    counts = {4: 2, 6: 0, 8: 0, 10: 1}
    out = allocate_tables(12, counts.copy(), max_tables=2)
    assert out is not None
    assert sum(out) >= 12
    assert len(out) <= 2


def test_multiset_subtract():
    maps = [{6: 1, 4: 2}, {6: 1, 4: 2}]
    assert multiset_subtract(maps, [6]) is True
    assert multiset_subtract(maps, [6, 4]) is True
    assert multiset_subtract(maps, [6, 6]) is False
