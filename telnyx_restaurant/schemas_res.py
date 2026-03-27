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
    "selected_dishes",
    "order_items",
    "food_items",
    "basket",
    "meal_selection",
    "preorder_lines",
    "dish_selection",
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
        "selected_dishes",
        "order_items",
        "food_items",
        "basket",
        "meal_selection",
        "preorder_lines",
        "dish_selection",
        "selected_items",
    }
)

_WRAP_KEYS_RESERVATION = (
    "data",
    "body",
    "payload",
    "reservation",
    "input",
    "parameters",
    "variables",
    "context",
    "tool_input",
    "arguments",
    "args",
    "result",
    "response",
    "attributes",
    "message",
    "content",
    "tool_output",
    "output",
)


def _unwrap_single_key_tool_dict(data: dict[str, Any]) -> dict[str, Any]:
    """{ \"create_reservation\": { ... } } or { \"booking\": { guest_name: ... } }."""
    if len(data) != 1:
        return data
    sole_val = next(iter(data.values()))
    if isinstance(sole_val, dict) and _RES_KEYS_HINT.intersection(sole_val):
        return dict(sole_val)
    return data


def _dict_has_positive_qty(d: dict[str, Any]) -> bool:
    for k in ("quantity", "qty", "count", "amount", "number"):
        v = d.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and v > 0:
            return True
        if isinstance(v, str) and v.strip().isdigit() and int(v.strip()) > 0:
            return True
    return False


def _dict_has_item_ref(d: dict[str, Any]) -> bool:
    for k in (
        "menu_item_id",
        "id",
        "sku",
        "item_id",
        "menuItemId",
        "menuId",
        "dish",
        "name",
        "item",
        "title",
        "menu_item",
        "dishName",
        "label",
        "description",
    ):
        v = d.get(k)
        if v is not None and str(v).strip():
            return True
    return False


def _is_preorder_line_dict(d: dict[str, Any]) -> bool:
    if not _dict_has_item_ref(d):
        return False
    if _dict_has_positive_qty(d):
        return True
    # Single-dish entries often omit quantity (implies 1).
    return True


def _looks_like_preorder_lines(lst: list[Any]) -> bool:
    if len(lst) < 1:
        return False
    dicts = [x for x in lst if isinstance(x, dict)]
    if not dicts:
        return False
    if len(dicts) < max(1, (len(lst) + 1) // 2):
        return False
    hits = sum(1 for x in dicts if _is_preorder_line_dict(x))
    return hits >= max(1, int(len(dicts) * 0.5))


def _longest_preorder_like_list_in_tree(obj: Any, depth: int = 0) -> list[Any] | None:
    """Find the longest list of dicts that looks like menu lines anywhere in the payload."""
    if depth > 14:
        return None
    best: list[Any] | None = None
    if isinstance(obj, dict):
        for v in obj.values():
            sub = _longest_preorder_like_list_in_tree(v, depth + 1)
            if sub and (best is None or len(sub) > len(best)):
                best = sub
    elif isinstance(obj, list):
        if _looks_like_preorder_lines(obj):
            return obj
        for v in obj:
            sub = _longest_preorder_like_list_in_tree(v, depth + 1)
            if sub and (best is None or len(sub) > len(best)):
                best = sub
    return best


def _inject_best_scavenged_preorder(d: dict[str, Any]) -> dict[str, Any]:
    """If no preorder list at top level, mine nested structures (LLM tool blobs)."""
    cur = d.get("preorder")
    if isinstance(cur, list) and len(cur) > 0:
        return d
    if isinstance(cur, dict):
        return d
    if isinstance(cur, str) and cur.strip():
        return d
    cand = _longest_preorder_like_list_in_tree(d)
    if not cand:
        return d
    out = dict(d)
    out["preorder"] = cand
    return out


def _unwrap_nested_reservation_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Merge Telnyx-style wrappers (possibly nested) so inner cart/name/phone win over empty roots."""
    d = _unwrap_single_key_tool_dict(dict(data))
    for _ in range(16):
        merged_layer = False
        for key in _WRAP_KEYS_RESERVATION:
            inner = d.get(key)
            if not isinstance(inner, dict):
                continue
            if not _RES_KEYS_HINT.intersection(inner):
                continue
            outer_rest = {k: v for k, v in d.items() if k != key}
            d = {**outer_rest, **inner}
            merged_layer = True
            break
        if not merged_layer:
            break
    d = _inject_best_scavenged_preorder(d)
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
            "selected_dishes",
            "order_items",
            "food_items",
            "basket",
            "meal_selection",
            "preorder_lines",
            "dish_selection",
            "selected_items",
        ):
            inner = v.get(key)
            if isinstance(inner, list):
                return inner
        return [v]
    if isinstance(v, list):
        out: list[Any] = []
        for el in v:
            if el is None or el == "":
                continue
            if isinstance(el, str):
                s = el.strip()
                if not s:
                    continue
                if s.startswith("{") or s.startswith("["):
                    try:
                        parsed = json.loads(s)
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, dict):
                        out.append(parsed)
                        continue
                    if isinstance(parsed, list):
                        out.extend(_coerce_preorder_value_to_lines(parsed))
                        continue
                # Telnyx "array(string)" tools: one string per line → single menu id, qty 1
                out.append({"menu_item_id": s, "quantity": 1})
            else:
                out.append(el)
        return out
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
        if v is None or v == "":
            return "online"
        if isinstance(v, str):
            s = v.strip().lower()
        else:
            s = str(v).strip().lower()
        if s in ("online", "voice", "api"):
            return s
        if s in ("ai", "assistant", "telnyx", "agent", "tool", "automation", "ivr", "bot"):
            return "api"
        return "api"


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
            "reservationStatus",
            "booking_status",
            "action",
            "Action",
            "operation",
            "Operation",
            "intent",
            "command",
            "event",
            "type",
            "input",
            "value",
            "outcome",
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
        for wrap in (
            "data",
            "reservation",
            "payload",
            "body",
            "attributes",
            "result",
            "input",
            "parameters",
            "variables",
            "context",
            "tool_arguments",
            "arguments",
            "args",
        ):
            inner = d.get(wrap)
            if isinstance(inner, dict):
                for k, v in inner.items():
                    d.setdefault(k, v)
            elif isinstance(inner, str) and inner.strip().startswith("{"):
                try:
                    parsed = json.loads(inner)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
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
