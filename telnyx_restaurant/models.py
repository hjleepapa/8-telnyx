"""ORM models for Hanok Table reservations."""

from __future__ import annotations

import enum
import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from telnyx_restaurant.db import Base


class ReservationStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    seated = "seated"
    completed = "completed"
    cancelled = "cancelled"


class TableSlotInventory(Base):
    """Per time-bucket, per table-size counts (combinable tables of same nominal size)."""

    __tablename__ = "table_slot_inventory"
    __table_args__ = (UniqueConstraint("slot_start", "table_size", name="uq_table_slot_inventory_slot_size"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Naive UTC wall time so sqlite tests and Postgres agree on equality in inventory queries.
    slot_start: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    table_size: Mapped[int] = mapped_column(Integer, index=True)
    available_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Reservation(Base):
    __tablename__ = "reservations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    confirmation_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    guest_name: Mapped[str] = mapped_column(String(255))
    guest_phone: Mapped[str] = mapped_column(String(64), index=True)
    party_size: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), index=True, default=ReservationStatus.confirmed.value)
    special_requests: Mapped[str | None] = mapped_column(Text, nullable=True)
    preorder_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    food_subtotal_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    preorder_discount_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    food_total_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source_channel: Mapped[str] = mapped_column(
        String(32), default="online", server_default="online", index=True
    )
    preferred_locale: Mapped[str] = mapped_column(
        String(16), default="en", server_default="en"
    )
    reminder_call_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Seating / table allocation (optional feature via HANOK_TABLE_ALLOCATION_ENABLED)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=120, server_default="120")
    tables_allocated_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    guest_priority: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    seating_status: Mapped[str] = mapped_column(
        String(32),
        default="not_applicable",
        server_default="not_applicable",
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    @property
    def preorder_items(self) -> list[dict[str, Any]]:
        if not self.preorder_json:
            return []
        try:
            data = json.loads(self.preorder_json)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    @property
    def tables_allocated(self) -> list[int] | None:
        if not self.tables_allocated_json:
            return None
        try:
            data = json.loads(self.tables_allocated_json)
            if isinstance(data, list):
                return [int(x) for x in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return None
