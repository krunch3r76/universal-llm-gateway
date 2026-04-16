"""Shared cortex-api relay helper — breaks the import cycle between cortex modules."""

from __future__ import annotations

import logging
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 30.0


def _cx(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Relay to cortex-api via UDS, normalizing error shape.

    Preserves the response body in the error message so callers can
    distinguish application-level errors (e.g. 404 entity-not-found)
    from routing errors (e.g. 404 route-not-found).
    """
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT) as client:
            response = client.request(method.upper(), path, json=body)
    except Exception as exc:
        logger.error("cortex-api relay failed: %s %s — %s", method, path, exc)
        return {"error": f"cortex-api connection failed: {exc}"}

    if response.status_code >= 400:
        detail = response.text
        msg = f"cortex-api error: HTTP {response.status_code}"
        if detail:
            msg += f" — {detail}"
        return {"error": msg, **({} if not detail else {"detail": detail})}

    try:
        return response.json()
    except Exception:
        return {"error": f"cortex-api returned invalid JSON: {response.text[:200]}"}
