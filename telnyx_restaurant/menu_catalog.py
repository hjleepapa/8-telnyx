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

# Short names voice/LLM tools often send instead of canonical ids
MENU_SLUG_ALIASES: dict[str, str] = {
    "bibimbap": "dolsot_bibimbap",
    "dolsot": "dolsot_bibimbap",
    "kimchi": "kimchi_jjigae",
    "kimchi jjigae": "kimchi_jjigae",
    "jjigae": "kimchi_jjigae",
    "bulgogi": "bulgogi",
    "bbq": "hanwoo_bbq",
    "hanwoo": "hanwoo_bbq",
    "korean bbq": "hanwoo_bbq",
    "pajeon": "haemul_pajeon",
    "seafood pancake": "haemul_pajeon",
    "naengmyeon": "naengmyeon_mandu",
    "mandu": "naengmyeon_mandu",
    "cold noodles": "naengmyeon_mandu",
}


def resolve_menu_item_id(menu_item_id: str | None, dish_name: str | None) -> str:
    """Map webhook/voice payloads to a catalog id (exact id, slug alias, or fuzzy name)."""
    candidates: list[str] = []
    for p in (menu_item_id, dish_name):
        if p and str(p).strip():
            candidates.append(str(p).strip())

    if not candidates:
        raise ValueError("Each pre-order line needs menu_item_id or dish_name when quantity > 0")

    for raw in candidates:
        rid = raw.lower().replace("-", "_").replace(" ", "_")
        if rid in MENU_BY_ID:
            return rid
        slug = raw.lower().strip()
        if slug in MENU_SLUG_ALIASES:
            return MENU_SLUG_ALIASES[slug]
        # "kimchi jjigae" style → "kimchi_jjigae"
        if slug.replace(" ", "_") in MENU_BY_ID:
            return slug.replace(" ", "_")

        for mid, item in MENU_BY_ID.items():
            if raw.lower() == mid.lower():
                return mid
            if raw.lower() == item.name_en.lower():
                return mid

        raw_l = raw.lower()
        scored: list[tuple[int, str]] = []
        for mid, item in MENU_BY_ID.items():
            en = item.name_en.lower()
            score = 0
            if raw_l == mid:
                score = 100
            elif raw_l in mid or mid.endswith(raw_l) or raw_l in en:
                score = max(len(raw_l), 5)
            elif any(t and t in en for t in raw_l.replace(",", " ").split() if len(t) > 3):
                score = 4
            if score:
                scored.append((score, mid))
        if scored:
            scored.sort(key=lambda x: (-x[0], -len(x[1])))
            return scored[0][1]

    raise ValueError(
        f"Unknown menu item {candidates[0]!r}; use id from GET /api/reservations/menu/items"
    )
