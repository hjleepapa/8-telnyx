"""Build stored preorder JSON and totals (7% discount when pre-ordering any item)."""

from __future__ import annotations

import json
from typing import Any

from telnyx_restaurant.menu_catalog import MENU_BY_ID, PREORDER_DISCOUNT_RATE
from telnyx_restaurant.schemas_res import PreorderLineIn


def lines_from_input(lines: list[PreorderLineIn]) -> list[dict[str, Any]]:
    """Normalize lines, validate menu ids, aggregate quantities."""
    agg: dict[str, int] = {}
    for line in lines:
        if line.quantity <= 0:
            continue
        if line.menu_item_id not in MENU_BY_ID:
            raise ValueError(f"Unknown menu item: {line.menu_item_id}")
        agg[line.menu_item_id] = agg.get(line.menu_item_id, 0) + line.quantity

    out: list[dict[str, Any]] = []
    for menu_id, qty in sorted(agg.items()):
        item = MENU_BY_ID[menu_id]
        line_total = qty * item.price_cents
        out.append(
            {
                "menu_item_id": menu_id,
                "name_en": item.name_en,
                "quantity": qty,
                "unit_price_cents": item.price_cents,
                "line_total_cents": line_total,
            }
        )
    return out


def totals_for_lines(stored_lines: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return subtotal_cents, discount_cents, total_cents."""
    if not stored_lines:
        return 0, 0, 0
    subtotal = sum(int(x["line_total_cents"]) for x in stored_lines)
    discount = int(round(subtotal * PREORDER_DISCOUNT_RATE))
    total = subtotal - discount
    return subtotal, discount, total


def preorder_summary_text(stored_lines: list[dict[str, Any]]) -> str:
    if not stored_lines:
        return ""
    parts = []
    for x in stored_lines:
        parts.append(f'{x["name_en"]} x{x["quantity"]}')
    return "; ".join(parts)


def serialize_preorder(lines: list[PreorderLineIn]) -> tuple[str | None, int, int, int]:
    """JSON string for DB column (or None), subtotal, discount, food total."""
    normalized = lines_from_input(lines)
    if not normalized:
        return None, 0, 0, 0
    subtotal, discount, total = totals_for_lines(normalized)
    return json.dumps(normalized), subtotal, discount, total


def parse_preorder_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []
