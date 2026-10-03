"""Unit tests for shared query client error envelopes."""

from __future__ import annotations

import httpx

from event_store.query_client_errors import (
    ERROR_CLASS_CLIENT_DEADLINE,
    ERROR_CLASS_LOCK_WAIT,
    envelope_client_deadline,
    envelope_from_http_status_error,
)


def test_envelope_from_http_status_error_lock_wait() -> None:
    request = httpx.Request("POST", "http://localhost/api/v1/observability/sql")
    response = httpx.Response(
        503,
        json={
            "error": "Event store waited on a database lock: database is locked",
            "error_class": ERROR_CLASS_LOCK_WAIT,
        },
        request=request,
    )
    exc = httpx.HTTPStatusError("busy", request=request, response=response)
    env = envelope_from_http_status_error(exc)
    assert env is not None
    assert env["error_class"] == ERROR_CLASS_LOCK_WAIT
    assert "wait" in env["error"].lower()


def test_envelope_client_deadline_code() -> None:
    env = envelope_client_deadline()
    assert env["error_class"] == ERROR_CLASS_CLIENT_DEADLINE
    assert "10s client deadline" in env["error"]
    assert "query_completed_age_ms" in env["error"]
    assert "narrower query" not in env["error"].lower()
