"""Table inventory locking, allocation, waitlist promotion — optional via HANOK_TABLE_ALLOCATION_ENABLED."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from sqlalchemy import Select, case, func, literal, or_, select
from sqlalchemy.orm import Session

from telnyx_restaurant.config import (
    hanok_default_reservation_duration_minutes,
    hanok_max_tables_per_party,
    hanok_reservation_verbose_logging,
    hanok_slot_step_minutes,
    hanok_table_allocation_enabled,
    hanok_table_inventory_template,
    hanok_vip_preorder_threshold_cents,
    hanok_waitlist_max_per_slot,
    hanok_waitlist_minutes_per_position,
)
from telnyx_restaurant.models import Reservation, ReservationStatus, TableSlotInventory
from telnyx_restaurant.reminders import schedule_reminder_on_table_allocated
from telnyx_restaurant.table_allocation import (
    allocate_tables,
    effective_counts_across_slots,
    floor_slot_start,
    iter_occupied_slots,
    multiset_subtract,
    summarize_inventory_for_log,
)

logger = logging.getLogger(__name__)


class SeatingUnavailableError(Exception):
    """Not enough contiguous inventory across duration; no waitlist allowed."""


def _norm_dt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _inv_slot(dt: datetime) -> datetime:
    """Naive UTC datetime for table_slot_inventory.slot_start (DB-agnostic equality)."""
    return _norm_dt(dt).replace(microsecond=0, tzinfo=None)


def effective_priority_for_row(declared: str, food_total_cents: int) -> str:
    """Return stored tier for the reservation row.

    - Explicit ``guest_priority`` **vip** from the client always wins.
    - Otherwise **vip** when ``food_total_cents`` ≥ ``HANOK_VIP_PREORDER_CENTS`` (default $500).
    - Else **normal**.
    """
    d = (declared or "normal").strip().lower()
    if d == "vip":
        return "vip"
    if int(food_total_cents or 0) >= hanok_vip_preorder_threshold_cents():
        return "vip"
    return "normal"


def sync_guest_priority_from_spend(row: Reservation) -> None:
    """Refresh ``row.guest_priority`` from current cart totals (call after create or preorder amend)."""
    row.guest_priority = effective_priority_for_row(
        row.guest_priority or "normal",
        int(row.food_total_cents or 0),
    )


def _rows_to_maps(
    slot_order: list[datetime],
    rows: list[TableSlotInventory],
) -> list[dict[int, int]]:
    by_key: dict[datetime, dict[int, int]] = {_inv_slot(s): {} for s in slot_order}
    for r in rows:
        rk = _inv_slot(r.slot_start)
        if rk in by_key:
            by_key[rk][r.table_size] = r.available_count
    return [by_key[_inv_slot(s)] for s in slot_order]


def ensure_inventory_for_slots(
    db: Session,
    slots: list[datetime],
    template: dict[int, int] | None = None,
) -> None:
    """Create missing (slot_start, table_size) rows with template counts; never overwrites existing."""
    tmpl = template or hanok_table_inventory_template()
    if not tmpl:
        return
    for slot in slots:
        slot_key = _inv_slot(slot)
        for size, count in sorted(tmpl.items()):
            existing = (
                db.execute(
                    select(TableSlotInventory).where(
                        TableSlotInventory.slot_start == slot_key,
                        TableSlotInventory.table_size == int(size),
                    )
                )
                .scalar_one_or_none()
            )
            if existing is None:
                db.add(
                    TableSlotInventory(
                        slot_start=slot_key,
                        table_size=int(size),
                        available_count=int(count),
                    )
                )
            elif existing.available_count < 0:
                existing.available_count = 0
    db.flush()


def lock_inventory_rows(db: Session, slots: list[datetime]) -> list[TableSlotInventory]:
    slots_n = [_inv_slot(s) for s in slots]
    if not slots_n:
        return []
    stmt: Select[tuple[TableSlotInventory]] = (
        select(TableSlotInventory)
        .where(TableSlotInventory.slot_start.in_(slots_n))
        .order_by(TableSlotInventory.slot_start, TableSlotInventory.table_size)
        .with_for_update()
    )
    return list(db.execute(stmt).scalars().all())


def _apply_delta_to_locked(
    slots: list[datetime],
    allocation: list[int],
    rows: list[TableSlotInventory],
    sign: int,
) -> None:
    """sign=+1 release, sign=-1 consume."""
    slots_n = [_inv_slot(s) for s in slots]
    need_per_slot = Counter(allocation)
    for slot in slots_n:
        for size, n in need_per_slot.items():
            for r in rows:
                if r.slot_start == slot and r.table_size == size:
                    r.available_count = r.available_count + sign * n
                    if r.available_count < 0:
                        raise RuntimeError(
                            f"Inventory underflow slot={slot} size={size} available={r.available_count}"
                        )
                    break
            else:
                raise RuntimeError(f"No inventory row for slot={slot} size={size}")


def release_allocation(
    db: Session,
    starts_at: datetime,
    duration_minutes: int,
    allocation: list[int],
) -> None:
    step = hanok_slot_step_minutes()
    slots = iter_occupied_slots(starts_at, duration_minutes, step)
    ensure_inventory_for_slots(db, slots)
    rows = lock_inventory_rows(db, slots)
    _apply_delta_to_locked(slots, allocation, rows, +1)


def try_allocate_and_consume(
    db: Session,
    party_size: int,
    starts_at: datetime,
    duration_minutes: int,
) -> list[int] | None:
    step = hanok_slot_step_minutes()
    slots = iter_occupied_slots(starts_at, duration_minutes, step)
    tmpl = hanok_table_inventory_template()
    need_rows = len(slots) * len(tmpl) if tmpl else 0
    maps: list[dict[int, int]] = []
    rows: list[TableSlotInventory] = []
    for _attempt in range(2):
        ensure_inventory_for_slots(db, slots, tmpl)
        db.flush()
        rows = lock_inventory_rows(db, slots)
        maps = _rows_to_maps(slots, rows)
        incomplete = bool(tmpl) and (
            any(len(m) == 0 for m in maps) or len(rows) < need_rows
        )
        if not incomplete:
            break

    eff = effective_counts_across_slots(maps)
    alloc = allocate_tables(
        party_size,
        {k: v for k, v in eff.items()},
        max_tables=hanok_max_tables_per_party(),
    )
    if not alloc or not multiset_subtract(maps, alloc):
        if hanok_reservation_verbose_logging():
            logger.info(
                "try_allocate_and_consume: no allocation party=%s slots=%s eff=%s maps=%s",
                party_size,
                [_inv_slot(s).isoformat() for s in slots],
                dict(sorted(eff.items())),
                summarize_inventory_for_log(maps),
            )
        return None
    _apply_delta_to_locked(slots, alloc, rows, -1)
    return alloc


@dataclass
class BookOnCreateResult:
    seating_status: str
    tables_allocated: list[int] | None


def _waitlist_ordered_for_slot(
    db: Session,
    *,
    starts_at: datetime,
    duration_minutes: int,
) -> list[Reservation]:
    """Waitlisted rows for the same floored slot and duration bucket as ``promote_waitlist`` (VIP / spend tier, then created_at).

    Uses ``_norm_dt`` for all ``starts_at`` values so queue membership matches across environments (naive vs aware DB values).
    Duration matching uses ``coalesce(duration_minutes, default)`` so legacy rows align with the configured default stay length.
    """
    step = hanok_slot_step_minutes()
    t0 = floor_slot_start(_norm_dt(starts_at), step)
    d = int(duration_minutes) if duration_minutes else hanok_default_reservation_duration_minutes()
    default_d = hanok_default_reservation_duration_minutes()
    threshold = hanok_vip_preorder_threshold_cents()
    is_priority = or_(
        Reservation.guest_priority == "vip",
        Reservation.food_total_cents >= threshold,
    )
    prio = case((is_priority, 0), else_=1)
    candidates = (
        db.execute(
            select(Reservation)
            .where(
                Reservation.seating_status == "waitlist",
                Reservation.status != ReservationStatus.cancelled.value,
                func.coalesce(Reservation.duration_minutes, literal(default_d)) == d,
            )
            .order_by(prio, Reservation.created_at)
        )
        .scalars()
        .all()
    )
    return [w for w in candidates if floor_slot_start(_norm_dt(w.starts_at), step) == t0]


def book_on_create(db: Session, row: Reservation, *, waitlist_ok: bool) -> BookOnCreateResult:
    if not hanok_table_allocation_enabled():
        row.seating_status = "not_applicable"
        row.tables_allocated_json = None
        return BookOnCreateResult("not_applicable", None)

    row.starts_at = _norm_dt(row.starts_at)
    if not row.duration_minutes or row.duration_minutes < 1:
        row.duration_minutes = hanok_default_reservation_duration_minutes()

    alloc = try_allocate_and_consume(db, row.party_size, row.starts_at, row.duration_minutes)
    if alloc:
        row.tables_allocated_json = json.dumps(alloc)
        row.seating_status = "allocated"
        logger.info(
            "Seating allocated reservation_id=%s tables=%s party=%s",
            row.id,
            alloc,
            row.party_size,
        )
        return BookOnCreateResult("allocated", alloc)

    if waitlist_ok:
        cap = hanok_waitlist_max_per_slot()
        n_existing = len(
            _waitlist_ordered_for_slot(db, starts_at=row.starts_at, duration_minutes=int(row.duration_minutes))
        )
        if n_existing >= cap:
            raise SeatingUnavailableError(
                "Waitlist is full for this seating window "
                f"({cap} parties maximum). Suggest the same party size about two hours earlier "
                "or about two hours later, if available."
            )
        row.tables_allocated_json = None
        row.seating_status = "waitlist"
        logger.info(
            "Seating waitlist reservation_id=%s party=%s", row.id, row.party_size
        )
        return BookOnCreateResult("waitlist", None)

    raise SeatingUnavailableError(
        "No table capacity for this party size and time window (try another time or enable waitlist)."
    )


def reseat_reservation_after_amend(
    db: Session,
    row: Reservation,
    *,
    old_starts_at: datetime,
    old_party_size: int,
    old_duration_minutes: int,
    old_seating_status: str,
    old_tables_allocated_json: str | None,
) -> None:
    """Release inventory for the previous slot when time/party changes, promote waitlist there, then allocate at the new slot."""
    if not hanok_table_allocation_enabled():
        return
    step = hanok_slot_step_minutes()
    old_t0 = floor_slot_start(_norm_dt(old_starts_at), step)
    new_t0 = floor_slot_start(_norm_dt(row.starts_at), step)
    d_old = (
        int(old_duration_minutes)
        if old_duration_minutes
        else hanok_default_reservation_duration_minutes()
    )
    d_new = (
        int(row.duration_minutes)
        if row.duration_minutes
        else hanok_default_reservation_duration_minutes()
    )
    if old_t0 == new_t0 and d_old == d_new and old_party_size == row.party_size:
        # Same slot/party: skip inventory churn, but waitlist order/capacity may have changed
        # (e.g. preorder crossed VIP spend threshold, or explicit priority patch) — retry promotion.
        if (
            row.seating_status == "waitlist"
            and row.status != ReservationStatus.cancelled.value
        ):
            promote_waitlist(db, row.starts_at, d_new)
        return

    if old_seating_status == "allocated" and old_tables_allocated_json:
        allocation: list[int] | None = None
        try:
            raw = json.loads(old_tables_allocated_json)
            if isinstance(raw, list):
                allocation = [int(x) for x in raw]
            else:
                raise ValueError("not a list")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning(
                "reseat_after_amend: bad old tables_allocated_json id=%s err=%s", row.id, e
            )
        if allocation is not None:
            try:
                release_allocation(db, old_starts_at, d_old, allocation)
            except Exception:
                logger.exception("reseat_after_amend: release_allocation failed reservation_id=%s", row.id)
        promote_waitlist(db, old_starts_at, d_old)

    row.tables_allocated_json = None
    row.seating_status = "not_applicable"

    alloc = try_allocate_and_consume(db, row.party_size, row.starts_at, d_new)
    if alloc:
        row.tables_allocated_json = json.dumps(alloc)
        row.seating_status = "allocated"
        row.updated_at = datetime.now(UTC)
        logger.info(
            "Reseat after amend reservation_id=%s tables=%s party=%s",
            row.id,
            alloc,
            row.party_size,
        )
        db.flush()
        if old_seating_status == "waitlist":
            try:
                schedule_reminder_on_table_allocated(row)
            except Exception:
                logger.exception(
                    "Reseat reminder scheduling failed reservation_id=%s", row.id
                )
        return

    cap = hanok_waitlist_max_per_slot()
    n_existing = len(_waitlist_ordered_for_slot(db, starts_at=row.starts_at, duration_minutes=d_new))
    if n_existing >= cap:
        raise SeatingUnavailableError(
            "Waitlist is full for this seating window "
            f"({cap} parties maximum). Suggest the same party size about two hours earlier "
            "or about two hours later, if available."
        )
    row.tables_allocated_json = None
    row.seating_status = "waitlist"
    row.updated_at = datetime.now(UTC)
    logger.info(
        "Reseat after amend -> waitlist reservation_id=%s party=%s",
        row.id,
        row.party_size,
    )
    db.flush()


def release_and_promote_after_cancel(db: Session, row: Reservation) -> None:
    if not hanok_table_allocation_enabled():
        return
    if row.seating_status != "allocated" or not row.tables_allocated_json:
        promote_waitlist(db, row.starts_at, row.duration_minutes)
        return
    try:
        allocation = json.loads(row.tables_allocated_json)
        if not isinstance(allocation, list):
            raise ValueError("not a list")
        allocation = [int(x) for x in allocation]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(
            "release_and_promote: bad tables_allocated_json id=%s err=%s", row.id, e
        )
        row.tables_allocated_json = None
        row.seating_status = "not_applicable"
        promote_waitlist(db, row.starts_at, row.duration_minutes)
        return

    try:
        release_allocation(db, row.starts_at, row.duration_minutes, allocation)
    except Exception:
        logger.exception("release_allocation failed reservation_id=%s", row.id)
    row.tables_allocated_json = None
    row.seating_status = "not_applicable"
    promote_waitlist(db, row.starts_at, row.duration_minutes)


def waitlist_queue_metadata(db: Session, row: Reservation) -> dict[str, int] | None:
    """1-based position in the same promotion queue as ``promote_waitlist`` (slot + duration bucket), plus queue size.

    Estimated wait minutes = position × ``HANOK_WAITLIST_MINUTES_PER_POSITION`` (default 15), e.g. 1st → 15, 2nd → 30, …
    """
    if not hanok_table_allocation_enabled():
        return None
    if (row.seating_status or "").strip() != "waitlist":
        return None
    if row.status == ReservationStatus.cancelled.value:
        return None
    # Same transaction may have updated seating_status without autoflush; visibility for SELECT.
    db.flush()
    d = int(row.duration_minutes) if row.duration_minutes else hanok_default_reservation_duration_minutes()
    ordered = _waitlist_ordered_for_slot(db, starts_at=row.starts_at, duration_minutes=d)
    ids = [w.id for w in ordered]
    if row.id not in ids:
        return None
    pos = ids.index(row.id) + 1
    n = len(ordered)
    per = hanok_waitlist_minutes_per_position()
    est = pos * per
    return {"position": pos, "queue_size": n, "estimated_wait_minutes": est}


def promote_waitlist(db: Session, starts_at: datetime, duration_minutes: int) -> int:
    """Confirm waitlisted rows for the same floor-aligned start and duration (MVP)."""
    if not hanok_table_allocation_enabled():
        return 0
    d = int(duration_minutes) if duration_minutes else hanok_default_reservation_duration_minutes()
    candidates = _waitlist_ordered_for_slot(db, starts_at=starts_at, duration_minutes=d)
    promoted = 0
    for w in candidates:
        alloc = try_allocate_and_consume(db, w.party_size, w.starts_at, w.duration_minutes)
        if not alloc:
            # Smaller / different party-size may still fit freed capacity; don't block the queue.
            continue
        w.tables_allocated_json = json.dumps(alloc)
        w.seating_status = "allocated"
        w.updated_at = datetime.now(UTC)
        promoted += 1
        logger.info("Waitlist promoted reservation_id=%s tables=%s", w.id, alloc)
        try:
            schedule_reminder_on_table_allocated(w)
        except Exception:
            logger.exception(
                "Waitlist promotion reminder scheduling failed reservation_id=%s", w.id
            )
    if promoted:
        db.flush()
    return promoted


def snapshot_effective_availability(
    db: Session,
    slots: list[datetime],
) -> dict[str, dict[str, int]]:
    """For GET /seating/availability — no locks, best-effort read."""
    if not slots:
        return {}
    slots_n = [_inv_slot(s) for s in slots]
    rows = (
        db.execute(
            select(TableSlotInventory).where(TableSlotInventory.slot_start.in_(slots_n))
        )
        .scalars()
        .all()
    )
    by_slot: dict[datetime, dict[int, int]] = {s: {} for s in slots_n}
    for r in rows:
        rk = _inv_slot(r.slot_start)
        if rk in by_slot:
            by_slot[rk][r.table_size] = r.available_count
    out: dict[str, dict[str, int]] = {}
    for s in slots_n:
        eff_maps = [by_slot[s]]
        eff = effective_counts_across_slots(eff_maps) if eff_maps[0] else {}
        out[s.isoformat()] = {str(k): v for k, v in sorted(eff.items())}
    return out


def iter_day_slot_starts(day: datetime, step_minutes: int) -> list[datetime]:
    """All grid starts from 00:00..23:30 for the UTC calendar day of `day`."""
    d = _norm_dt(day).date()
    start = datetime(d.year, d.month, d.day, tzinfo=UTC)
    out: list[datetime] = []
    t = start
    end = start + timedelta(days=1)
    while t < end:
        out.append(_inv_slot(t))
        t += timedelta(minutes=step_minutes)
    return out
