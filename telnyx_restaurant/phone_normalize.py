"""Normalize phone strings for DB lookup (Telnyx ANI vs guest_phone on reservations)."""

from __future__ import annotations


def phone_lookup_variants(raw: str | None) -> list[str]:
    """Build a small set of strings that should match the same physical line."""
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    digits = "".join(c for c in s if c.isdigit())
    variants: set[str] = {s}
    if not digits:
        return [s]

    variants.add(digits)
    # North American: 10 digits or 11 with leading 1
    if len(digits) == 10:
        variants.add(f"+1{digits}")
        variants.add(f"1{digits}")
    elif len(digits) == 11 and digits.startswith("1"):
        rest = digits[1:]
        variants.add(rest)
        variants.add(f"+{digits}")
        variants.add(f"+1{rest}")
        variants.add(digits)

    # Drop obviously duplicate empty
    return sorted({v for v in variants if v})


def to_e164_us(raw: str) -> str:
    """Normalize to +E.164 for North America so Telnyx `to` / `from` validate."""
    s = (raw or "").strip()
    if not s:
        return s
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if s.startswith("+") and digits:
        return f"+{digits}"
    return s
