"""Shared client-side error envelopes for event-service /v1/query callers."""

from __future__ import annotations

from typing import Any

import httpx

ERROR_CLASS_LOCK_WAIT = "lock_wait"
ERROR_CLASS_CLIENT_DEADLINE = "client_deadline"


def lock_wait_body(server_error: str | None = None) -> dict[str, str]:
    """503 lock-wait JSON body for HTTP query responses."""
    detail = (server_error or "").strip()
    if detail:
        error = f"Event store waited on a database lock: {detail}"
    else:
        error = "Event store waited on a database lock."
    return {"error": error, "error_class": ERROR_CLASS_LOCK_WAIT}


def envelope_from_http_status_error(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    """Map a 503 lock_wait body to a stable client envelope, or None."""
    try:
        body = exc.response.json()
    except Exception:
        return None
    if body.get("error_class") != ERROR_CLASS_LOCK_WAIT:
        return None
    return {
        "error": body.get(
            "error",
            "Event store waited on a database lock.",
        ),
        "error_class": ERROR_CLASS_LOCK_WAIT,
    }


def envelope_client_deadline() -> dict[str, Any]:
    """Envelope when httpx hits the read deadline before the server responds."""
    return {
        "error": (
            "Event query read timed out: the 10s client deadline fired before "
            "the server responded. Check event_service /health query_path "
            "(event_loop_lag_ms, query_completed_age_ms)."
        ),
        "error_class": ERROR_CLASS_CLIENT_DEADLINE,
    }
