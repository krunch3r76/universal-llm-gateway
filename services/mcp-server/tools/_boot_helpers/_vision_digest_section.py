"""Boot card vision-digest section — thin wrapper over libs formatter."""

from __future__ import annotations

from typing import Any

from cortex_store.vision_digest import VisionDigest
from cortex_store.vision_digest_format import format_boot_card_md

_VISION_DIGEST_PATH = "/api/v1/doctrine/vision-digest"


def vision_digest_fetch_path() -> str:
    """Canonical GET path for boot parallel fetch."""
    return _VISION_DIGEST_PATH


def format_vision_digest_for_card(raw: dict[str, Any] | None) -> str | None:
    """Soft-fail: return markdown section or None when digest unavailable."""
    if not raw or raw.get("error") or raw.get("status_code"):
        return None
    pillars = raw.get("pillars")
    if not isinstance(pillars, list) or not pillars:
        return None
    try:
        digest = VisionDigest.model_validate(raw)
    except Exception:
        return None
    md = format_boot_card_md(digest).strip()
    return md or None
