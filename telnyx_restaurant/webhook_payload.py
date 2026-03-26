"""Extract caller identity from Telnyx dynamic-variable and ad-hoc webhook bodies."""

from __future__ import annotations

from typing import Any


def extract_caller_number(payload: dict[str, Any] | None) -> str | None:
    """Resolve caller / ANI from flat or Telnyx assistant.initialization-shaped JSON."""
    if not payload:
        return None

    def _take(v: Any) -> str | None:
        if isinstance(v, str):
            t = v.strip()
            return t or None
        return None

    # Flat keys (manual curl / older samples)
    for key in (
        "caller_number",
        "from",
        "From",
        "caller",
        "Caller",
        "telnyx_end_user_target",
        "customer_phone",
        "ani",
    ):
        got = _take(payload.get(key))
        if got:
            return got

    # Nested: { "data": { "payload": { "telnyx_end_user_target": "+1..." } } }
    data = payload.get("data")
    if isinstance(data, dict):
        inner = data.get("payload")
        if isinstance(inner, dict):
            for key in (
                "telnyx_end_user_target",
                "caller_number",
                "from",
            ):
                got = _take(inner.get(key))
                if got:
                    return got
        for key in ("caller_number", "from"):
            got = _take(data.get(key))
            if got:
                return got

    return None
