"""Map site / API language choice to stored locale and Telnyx assistant hints."""

from __future__ import annotations

from typing import Any


def normalize_preferred_locale(v: Any) -> str:
    """Persistable code: ``en`` or ``ko`` (voice + dynamic variables)."""
    if v is None:
        return "en"
    if isinstance(v, str):
        s = v.strip().lower().replace("_", "-")
        if not s:
            return "en"
        if s.startswith("ko"):
            return "ko"
        if s in ("en", "english", "us", "en-us"):
            return "en"
    return "en"


def assistant_locale_hint(stored: str) -> str:
    """BCP-47 tag for Telnyx instruction templates (e.g. ``{{locale_hint}}``)."""
    return "ko-KR" if (stored or "").strip().lower() == "ko" else "en-US"
