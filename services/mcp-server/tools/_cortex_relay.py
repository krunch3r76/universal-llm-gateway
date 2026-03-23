"""Shared cortex-api relay helper — breaks the import cycle between cortex modules."""

from __future__ import annotations

from typing import Any

from .local_api import _relay


def _cx(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Relay to cortex-api, normalizing error shape.

    Preserves the response body in the error message so callers can
    distinguish application-level errors (e.g. 404 entity-not-found)
    from routing errors (e.g. 404 route-not-found).
    """
    result = _relay("cortex-api", method, path, body=body)
    if "error" in result:
        detail = result.get("body", "")
        msg = f"cortex-api error: {result['error']}"
        if detail:
            msg += f" — {detail}"
        return {"error": msg, **({} if not detail else {"detail": detail})}
    return result
