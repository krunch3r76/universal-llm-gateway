"""Private configuration for web_tab observation (not generic persona defaults)."""

from __future__ import annotations

import os


def resolve_cdp_url(explicit: str | None) -> str:
    """Return configured CDP endpoint URL; explicit wins over env."""
    if explicit and str(explicit).strip():
        return str(explicit).strip()
    url = os.environ.get("WEB_TAB_CDP_URL", "").strip()
    if not url:
        raise ValueError("missing_cdp_url")
    return url
