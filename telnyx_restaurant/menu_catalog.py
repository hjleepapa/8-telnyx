"""Demo menu catalog for pre-order pricing (online reservation flow)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MenuItem:
    id: str
    name_en: str
    name_ko: str
    price_cents: int
    blurb_en: str

    def as_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name_en": self.name_en,
            "name_ko": self.name_ko,
            "price_cents": self.price_cents,
            "price_display": f"${self.price_cents / 100:.2f}",
            "description": self.blurb_en,
        }


MENU_ITEMS: tuple[MenuItem, ...] = (
    MenuItem(
        id="dolsot_bibimbap",
        name_en="Dolsot bibimbap",
        name_ko="돌솥 비빔밥",
        price_cents=2200,
        blurb_en="Stone pot rice bowl — seasonal vegetables.",
    ),
    MenuItem(
        id="kimchi_jjigae",
        name_en="Aged kimchi jjigae",
        name_ko="묵은지 김치찌개",
        price_cents=1900,
        blurb_en="Spicy kimchi stew.",
    ),
    MenuItem(
        id="bulgogi",
        name_en="Soy-marinated bulgogi",
        name_ko="양념 불고기",
        price_cents=2400,
        blurb_en="Sweet-savory grilled beef.",
    ),
    MenuItem(
        id="hanwoo_bbq",
        name_en="Charcoal hanwoo BBQ set",
        name_ko="숯불 한우 모둠",
        price_cents=8900,
        blurb_en="Premium Korean barbecue for the table.",
    ),
    MenuItem(
        id="haemul_pajeon",
        name_en="Haemul pajeon",
        name_ko="해물 파전",
        price_cents=1800,
        blurb_en="Seafood scallion pancake.",
    ),
    MenuItem(
        id="naengmyeon_mandu",
        name_en="Naengmyeon & mandu",
        name_ko="냉면 & 만두",
        price_cents=1600,
        blurb_en="Cold noodles and dumplings.",
    ),
)

MENU_BY_ID: dict[str, MenuItem] = {m.id: m for m in MENU_ITEMS}

PREORDER_DISCOUNT_RATE = 0.07
