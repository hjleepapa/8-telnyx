"""ORM models for Hanok Table reservations."""

from __future__ import annotations

import enum
import json
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from telnyx_restaurant.db import Base


class ReservationStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    seated = "seated"
    completed = "completed"
    cancelled = "cancelled"


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
    reminder_call_status: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
