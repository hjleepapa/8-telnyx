"""Pydantic schemas for reservation API."""

from __future__ import annotations

import json
from typing import Any

from datetime import datetime

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator


class PreorderLineIn(BaseModel):
    """Telnyx/webhook tools often send id/qty/name instead of menu_item_id/quantity."""

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}

    menu_item_id: str | None = Field(
        None,
        max_length=64,
        validation_alias=AliasChoices(
            "menu_item_id",
            "id",
            "menu_id",
            "sku",
            "item_id",
            "menuItemId",
        ),
    )
    dish_name: str | None = Field(
        None,
        max_length=255,
        validation_alias=AliasChoices(
            "dish_name",
            "name",
            "item",
            "dish",
            "menu_item",
            "item_name",
            "menuItem",
            "dishName",
        ),
    )
    quantity: int = Field(
        0,
        ge=0,
        le=99,
        validation_alias=AliasChoices("quantity", "qty", "count", "amount"),
    )

    @model_validator(mode="after")
    def line_required_when_qty(self) -> PreorderLineIn:
        if self.quantity > 0 and not (self.menu_item_id or self.dish_name):
            raise ValueError("preorder line with quantity>0 needs menu_item_id or dish_name")
        return self


class ReservationCreate(BaseModel):
    guest_name: str = Field(..., min_length=1, max_length=255)
    guest_phone: str = Field(..., min_length=3, max_length=64)
    party_size: int = Field(..., ge=1, le=20)
    starts_at: datetime
    special_requests: str | None = Field(None, max_length=2000)
    preorder: list[PreorderLineIn] = Field(
        default_factory=list,
        validation_alias=AliasChoices("preorder", "pre_order", "menu_order", "preOrder"),
    )
    source_channel: str = Field(
        default="online",
        pattern="^(online|voice|api)$",
    )

    @field_validator("preorder", mode="before")
    @classmethod
    def coerce_preorder(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            try:
                v = json.loads(s)
            except json.JSONDecodeError:
                return []
        if isinstance(v, dict):
            return [v]
        return v

    @field_validator("source_channel", mode="before")
    @classmethod
    def lower_source_channel(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v


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
