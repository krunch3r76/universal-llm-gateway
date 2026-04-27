"""API response extraction utilities."""

from __future__ import annotations

from typing import Any


def safe_list(raw: dict[str, Any] | list[Any], key: str = "items") -> list[Any]:
    """Extract a list from an API response, returning [] on error."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "error" in raw:
            return []
        return raw.get(key, [])
    return []
