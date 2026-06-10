"""Shared cortex-api relay helper — breaks the import cycle between cortex modules."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp_events import monotonic_now, record
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_REQUEST_TIMEOUT = 30.0


def cx(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Relay to cortex-api via UDS, normalizing error shape.

    Success: returns the parsed JSON body as a dict.
    Failure: returns ``{"error": str, "status_code": int | None, "detail"?: str}``.
    ``status_code`` is the HTTP status integer for application-level errors
    (e.g. 404 entity-not-found, 409 conflict) and ``None`` for transport or
    JSON-parse failures. Callers branch on ``status_code`` directly rather
    than substring-matching the error string — see F14 in the master review.
    """
    method = method.upper()
    t0 = monotonic_now()
    record(
        "mcp.cortex.relay.called",
        method=method,
        path=path,
        timeout_s=_REQUEST_TIMEOUT,
    )

    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_REQUEST_TIMEOUT) as client:
            response = client.request(method, path, json=body)
    except httpx.RequestError as exc:
        duration = monotonic_now() - t0
        record(
            "mcp.cortex.relay.failed",
            method=method,
            path=path,
            error="request_error",
            duration_s=round(duration, 3),
            timeout_s=_REQUEST_TIMEOUT,
            detail=str(exc),
        )
        logger.error("cortex-api relay failed: %s %s — %s", method, path, exc)
        return {
            "error": f"cortex-api connection failed: {exc}",
            "status_code": None,
        }

    duration = monotonic_now() - t0

    if response.status_code >= 400:
        detail = response.text
        msg = f"cortex-api error: HTTP {response.status_code}"
        if detail:
            msg += f" — {detail}"
        record(
            "mcp.cortex.relay.failed",
            method=method,
            path=path,
            error="http_error",
            status_code=response.status_code,
            duration_s=round(duration, 3),
            timeout_s=_REQUEST_TIMEOUT,
            **({"detail": detail} if detail else {}),
        )
        result: dict[str, Any] = {
            "error": msg,
            "status_code": response.status_code,
        }
        if detail:
            result["detail"] = detail
        return result

    try:
        parsed = response.json()
    except (ValueError, json.JSONDecodeError):
        record(
            "mcp.cortex.relay.failed",
            method=method,
            path=path,
            error="invalid_json",
            duration_s=round(duration, 3),
            timeout_s=_REQUEST_TIMEOUT,
            detail=response.text[:200],
        )
        return {
            "error": f"cortex-api returned invalid JSON: {response.text[:200]}",
            "status_code": None,
        }

    record(
        "mcp.cortex.relay.completed",
        method=method,
        path=path,
        status_code=response.status_code,
        duration_s=round(duration, 3),
        timeout_s=_REQUEST_TIMEOUT,
    )
    return parsed
