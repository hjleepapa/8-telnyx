"""Pydantic schemas for reservation API."""

from __future__ import annotations

import json
import re
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


PREORDER_ALIASES = AliasChoices(
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
    "menu",
    "cart",
    "dishes",
)


def _lift_nested_preorder_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Promote nested menu/cart blobs so preorder validates as a list of lines."""
    d = dict(data)
    for root_key in ("preorder", "menu", "cart", "dishes"):
        inner_blk = d.get(root_key)
        if not isinstance(inner_blk, dict):
            continue
        for key in ("items", "lines", "menu", "entries", "preorder", "cart", "dishes"):
            nested = inner_blk.get(key)
            if isinstance(nested, list):
                d["preorder"] = nested
                break
    return d


_RES_KEYS_HINT = frozenset(
    {
        "guest_name",
        "guest_phone",
        "name",
        "phone",
        "starts_at",
        "party_size",
        "preorder",
        "pre_order",
        "items",
        "lines",
        "menu_order",
        "menu",
        "cart",
        "dishes",
        "food",
    }
)


def _unwrap_nested_reservation_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Merge Telnyx-style wrappers (data/body/payload/…) into one flat dict."""
    d = dict(data)
    for key in ("data", "body", "payload", "reservation", "input", "parameters"):
        inner = d.get(key)
        if not isinstance(inner, dict):
            continue
        if _RES_KEYS_HINT.intersection(inner):
            outer_rest = {k: v for k, v in d.items() if k != key}
            # Inner wins on conflicts — wrappers often have empty preorder/items at the root
            # while the real cart lives inside `data`.
            d = {**outer_rest, **inner}
            break
    return _lift_nested_preorder_dict(d)


def _coerce_preorder_value_to_lines(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            v = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError(f"preorder string must be valid JSON array or object: {e}") from e
    if isinstance(v, dict):
        for key in (
            "items",
            "lines",
            "preorder",
            "menu",
            "menu_items",
            "entries",
            "order",
            "dishes",
            "cart",
        ):
            inner = v.get(key)
            if isinstance(inner, list):
                return inner
        return [v]
    if isinstance(v, list):
        return v
    raise ValueError("preorder must be a list of lines or a wrapped object with an items/lines list")


class ReservationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    guest_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices(
            "guest_name",
            "name",
            "full_name",
            "customer_name",
            "guestName",
            "customer",
        ),
    )
    guest_phone: str = Field(
        ...,
        min_length=3,
        max_length=64,
        validation_alias=AliasChoices(
            "guest_phone",
            "phone",
            "mobile",
            "tel",
            "telephone",
            "guestPhone",
            "caller_number",
            "caller_id",
            "telnyx_end_user_target",
            "end_user_phone",
        ),
    )
    party_size: int = Field(..., ge=1, le=20)
    starts_at: datetime
    special_requests: str | None = Field(None, max_length=2000)
    preorder: list[PreorderLineIn] = Field(
        default_factory=list,
        validation_alias=PREORDER_ALIASES,
    )
    source_channel: str = Field(
        default="online",
        pattern="^(online|voice|api)$",
    )

    @field_validator("guest_phone", mode="before")
    @classmethod
    def guest_phone_coerce(cls, v: Any) -> Any:
        """Tools often send E.164 as JSON number; avoid precision loss by using int."""
        if v is None:
            return v
        if isinstance(v, bool):
            raise ValueError("guest_phone cannot be boolean")
        if isinstance(v, float):
            if abs(v - round(v)) < 1e-6 and abs(v) < 1e13:
                return str(int(round(v)))
            return str(v).strip()
        if isinstance(v, int):
            return str(v)
        return str(v).strip() if isinstance(v, str) else str(v)

    @field_validator("starts_at", mode="before")
    @classmethod
    def starts_at_date_only(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return f"{s}T18:00:00+00:00"
        return v

    @field_validator("party_size", mode="before")
    @classmethod
    def party_size_int(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v

    @model_validator(mode="before")
    @classmethod
    def unwrap_telnyx_payload_and_lift_preorder(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return _unwrap_nested_reservation_payload(data)

    @field_validator("preorder", mode="before")
    @classmethod
    def coerce_preorder(cls, v: Any) -> Any:
        if v is None:
            return []
        return _coerce_preorder_value_to_lines(v)

    @field_validator("source_channel", mode="before")
    @classmethod
    def lower_source_channel(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip().lower()
        return v


class ReservationUpdate(BaseModel):
    """Partial update (party size, time, pre-order, guest fields). Omit fields you do not change."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    guest_name: str | None = Field(
        None,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices(
            "guest_name",
            "name",
            "full_name",
            "customer_name",
            "guestName",
            "customer",
        ),
    )
    guest_phone: str | None = Field(
        None,
        min_length=3,
        max_length=64,
        validation_alias=AliasChoices(
            "guest_phone",
            "phone",
            "mobile",
            "tel",
            "telephone",
            "guestPhone",
            "caller_number",
            "caller_id",
            "telnyx_end_user_target",
            "end_user_phone",
        ),
    )
    party_size: int | None = Field(None, ge=1, le=20)
    starts_at: datetime | None = None
    special_requests: str | None = Field(None, max_length=2000)
    preorder: list[PreorderLineIn] | None = Field(None, validation_alias=PREORDER_ALIASES)

    @field_validator("guest_phone", mode="before")
    @classmethod
    def guest_phone_optional(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, bool):
            raise ValueError("guest_phone cannot be boolean")
        if isinstance(v, float):
            if abs(v - round(v)) < 1e-6 and abs(v) < 1e13:
                return str(int(round(v)))
            return str(v).strip()
        if isinstance(v, int):
            return str(v)
        return str(v).strip() if isinstance(v, str) else str(v)

    @field_validator("starts_at", mode="before")
    @classmethod
    def starts_at_date_only_optional(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        if isinstance(v, str):
            s = v.strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                return f"{s}T18:00:00+00:00"
        return v

    @field_validator("party_size", mode="before")
    @classmethod
    def party_size_int(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v

    @model_validator(mode="before")
    @classmethod
    def unwrap_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return _unwrap_nested_reservation_payload(data)

    @field_validator("preorder", mode="before")
    @classmethod
    def coerce_preorder_optional(cls, v: Any) -> Any:
        if v is None:
            return None
        return _coerce_preorder_value_to_lines(v)


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
