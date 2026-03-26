"""Pydantic schemas for reservation API."""

from __future__ import annotations

from typing import Any

from datetime import datetime

from pydantic import BaseModel, Field


class PreorderLineIn(BaseModel):
    menu_item_id: str = Field(..., min_length=1, max_length=64)
    quantity: int = Field(default=0, ge=0, le=99)


class ReservationCreate(BaseModel):
    guest_name: str = Field(..., min_length=1, max_length=255)
    guest_phone: str = Field(..., min_length=3, max_length=64)
    party_size: int = Field(..., ge=1, le=20)
    starts_at: datetime
    special_requests: str | None = Field(None, max_length=2000)
    preorder: list[PreorderLineIn] = Field(default_factory=list)
    source_channel: str = Field(
        default="online",
        pattern="^(online|voice|api)$",
    )


class ReservationRead(BaseModel):
    id: int
    confirmation_code: str
    guest_name: str
    guest_phone: str
    party_size: int
    starts_at: datetime
    status: str
    special_requests: str | None
    preorder_items: list[dict[str, Any]] = Field(default_factory=list)
    food_subtotal_cents: int = 0
    preorder_discount_cents: int = 0
    food_total_cents: int = 0
    source_channel: str = "online"
    reminder_call_status: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReservationStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|confirmed|seated|completed|cancelled)$")
