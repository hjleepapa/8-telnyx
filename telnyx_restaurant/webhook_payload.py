"""Extract caller identity from Telnyx dynamic-variable and ad-hoc webhook bodies."""

from __future__ import annotations

from typing import Any


def _looks_like_usable_phone_for_lookup(s: str) -> bool:
    """True if the string is plausible PSTN / E.164 for DB lookup (not a Telnyx SIP username).

    Telnyx may send ``from`` as ``opaque@gencred...@sip.telnyx.com`` while
    ``telnyx_end_user_target`` holds the real +E.164. Preferring usable tokens keeps
    ``_profile_from_db`` attached to ``food_total_cents`` for premium / cancel-retention variables.
    """
    t = (s or "").strip()
    if not t:
        return False
    lower = t.lower()
    if "@" in t:
        return lower.startswith("sip:") or lower.startswith("tel:")
    digits = "".join(c for c in t if c.isdigit())
    return len(digits) >= 10


def extract_caller_number(payload: dict[str, Any] | None) -> str | None:
    """Resolve caller / ANI from flat or Telnyx assistant.initialization-shaped JSON."""
    if not payload:
        return None

    def _take(v: Any) -> str | None:
        if isinstance(v, str):
            x = v.strip()
            return x or None
        return None

    cands: list[str] = []
    seen: set[str] = set()

    def _push(v: Any) -> None:
        got = _take(v)
        if got and got not in seen:
            seen.add(got)
            cands.append(got)

    # Nested first (official assistant shape): real number often only here when ``from`` is SIP opaque.
    data = payload.get("data")
    if isinstance(data, dict):
        inner = data.get("payload")
        if isinstance(inner, dict):
            for key in (
                "telnyx_end_user_target",
                "caller_number",
                "from",
            ):
                _push(inner.get(key))
        for key in ("caller_number", "from"):
            _push(data.get(key))

    for key in (
        "telnyx_end_user_target",
        "caller_number",
        "customer_phone",
        "ani",
        "from",
        "From",
        "caller",
        "Caller",
    ):
        _push(payload.get(key))

    for c in cands:
        if _looks_like_usable_phone_for_lookup(c):
            return c
    return cands[0] if cands else None
