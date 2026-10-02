"""Shared client-side error envelopes for event-service /v1/query callers."""

from __future__ import annotations

from typing import Any

import httpx

ERROR_CODE_SQLITE_BUSY = "sqlite_busy"
ERROR_CODE_CLIENT_DEADLINE = "client_deadline"


def envelope_from_http_status_error(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    """Map a 503 sqlite_busy body to a stable client envelope, or None."""
    try:
        body = exc.response.json()
    except Exception:
        return None
    if body.get("error_code") != ERROR_CODE_SQLITE_BUSY:
        return None
    return {
        "error": body.get(
            "error",
            "Event store database is locked (SQLITE_BUSY after busy_timeout).",
        ),
        "error_code": ERROR_CODE_SQLITE_BUSY,
    }


def envelope_client_deadline() -> dict[str, Any]:
    """Envelope when httpx hits the read deadline before the server responds."""
    return {
        "error": (
            "Event query timed out before the server responded. "
            "Check event_service /health query_path "
            "(event_loop_lag_ms, seconds_since_last_query) or retry with a narrower query."
        ),
        "error_code": ERROR_CODE_CLIENT_DEADLINE,
    }
