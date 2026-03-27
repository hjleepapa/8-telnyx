"""Pydantic schemas for reservation API."""

from __future__ import annotations

import json
from typing import Any

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


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

    @field_validator("menu_item_id", mode="before")
    @classmethod
    def menu_id_to_str(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        return str(v).strip()
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

    @field_validator("quantity", mode="before")
    @classmethod
    def quantity_coerce_int(cls, v: Any) -> Any:
        if v is None or v == "":
            return 0
        if isinstance(v, bool):
            return int(v)
        if isinstance(v, str) and v.strip():
            try:
                return int(v.strip())
            except ValueError:
                return v
        return v

    @model_validator(mode="after")
    def line_required_when_qty(self) -> PreorderLineIn:
        if self.quantity > 0 and not (self.menu_item_id or self.dish_name):
            raise ValueError("preorder line with quantity>0 needs menu_item_id or dish_name")
        return self


class ReservationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    guest_name: str = Field(..., min_length=1, max_length=255)
    guest_phone: str = Field(..., min_length=3, max_length=64)
    party_size: int = Field(..., ge=1, le=20)
    starts_at: datetime
    special_requests: str | None = Field(None, max_length=2000)
    preorder: list[PreorderLineIn] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "preorder",
            "pre_order",
            "menu_order",
            "preOrder",
            "preorder_items",
            "menu_items",
            "items",
            "lines",
            "order",
            "food",
            "selected_items",
        ),
    )
    source_channel: str = Field(
        default="online",
        pattern="^(online|voice|api)$",
    )

    @field_validator("party_size", mode="before")
    @classmethod
    def party_size_int(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v

    @model_validator(mode="before")
    @classmethod
    def lift_preorder_from_wrapper(cls, data: Any) -> Any:
        """Telnyx often sends preorder only as top-level `items` / nested { items: [...] }."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        inner_preorder = d.get("preorder")
        if isinstance(inner_preorder, dict):
            for key in ("items", "lines", "menu", "entries", "preorder"):
                nested = inner_preorder.get(key)
                if isinstance(nested, list):
                    d["preorder"] = nested
                    break
        return d

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
            except json.JSONDecodeError as e:
                raise ValueError(f"preorder string must be valid JSON array or object: {e}") from e
        if isinstance(v, dict):
            for key in ("items", "lines", "preorder", "menu", "menu_items", "entries", "order"):
                inner = v.get(key)
                if isinstance(inner, list):
                    return inner
            return [v]
        if isinstance(v, list):
            return v
        raise ValueError("preorder must be a list of lines or a wrapped object with an items/lines list")

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
    """Voice/webhook tools often send cancel/canceled, odd keys, nested JSON, or an empty body."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True, extra="ignore")

    status: str = Field(
        ...,
        validation_alias=AliasChoices(
            "status",
            "Status",
            "state",
            "new_status",
            "reservation_status",
            "action",
            "Action",
            "operation",
            "Operation",
            "intent",
            "command",
            "event",
            "type",
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def unwrap_nested_and_cancel_flag(cls, data: Any) -> Any:
        if data is None:
            return {"status": None}
        if isinstance(data, str):
            return {"status": data.strip()}
        if not isinstance(data, dict):
            return data

        d: dict[str, Any] = dict(data)
        for wrap in ("data", "reservation", "payload", "body", "attributes", "result", "input", "parameters"):
            inner = d.get(wrap)
            if isinstance(inner, dict):
                for k, v in inner.items():
                    d.setdefault(k, v)

        for flag in ("cancel", "Cancel", "cancel_reservation", "cancellation_requested"):
            v = d.get(flag)
            if v is True:
                d.setdefault("status", "cancelled")
            elif isinstance(v, str) and v.strip().casefold() in ("true", "1", "yes", "y"):
                d.setdefault("status", "cancelled")
        return d

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: Any) -> str:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            raise ValueError("status is required")
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        raw = str(v).strip()
        if not raw:
            raise ValueError("status is required")
        s = raw.casefold().replace(" ", "_").replace("-", "_")

        # Common LLM / US English variants → canonical
        if s in {"cancel", "canceled", "cancellation", "void", "voided", "delete", "deleted"}:
            return "cancelled"
        if s in {
            "confirm",
            "confirmed",
            "confirm_reservation",
        }:
            return "confirmed"
        if s in {"pending", "hold", "waitlist"}:
            return "pending"
        if s in {"seated", "seat", "arrived", "checked_in", "check_in"}:
            return "seated"
        if s in {"completed", "complete", "done", "finished", "closed"}:
            return "completed"
        if s in {"pending", "confirmed", "seated", "completed", "cancelled"}:
            return s

        raise ValueError(
            f"Invalid status {raw!r}; use pending, confirmed, seated, completed, or cancelled (cancel/canceled ok)."
        )
