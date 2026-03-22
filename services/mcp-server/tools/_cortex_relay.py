"""Shared cortex-api relay helper — breaks the import cycle between cortex modules."""

from __future__ import annotations

from typing import Any

from .local_api import _relay


def _cx(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Relay to cortex-api, normalizing error shape."""
    result = _relay("cortex-api", method, path, body=body)
    if "error" in result:
        return {"error": f"cortex-api error: {result['error']}"}
    return result
