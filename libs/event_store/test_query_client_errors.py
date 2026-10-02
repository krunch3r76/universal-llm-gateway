"""Unit tests for shared query client error envelopes."""

from __future__ import annotations

import httpx

from event_store.query_client_errors import (
    ERROR_CODE_CLIENT_DEADLINE,
    ERROR_CODE_SQLITE_BUSY,
    envelope_client_deadline,
    envelope_from_http_status_error,
)


def test_envelope_from_http_status_error_sqlite_busy() -> None:
    request = httpx.Request("POST", "http://localhost/v1/query")
    response = httpx.Response(
        503,
        json={"error": "database is locked", "error_code": ERROR_CODE_SQLITE_BUSY},
        request=request,
    )
    exc = httpx.HTTPStatusError("busy", request=request, response=response)
    env = envelope_from_http_status_error(exc)
    assert env is not None
    assert env["error_code"] == ERROR_CODE_SQLITE_BUSY


def test_envelope_client_deadline_code() -> None:
    env = envelope_client_deadline()
    assert env["error_code"] == ERROR_CODE_CLIENT_DEADLINE
    assert "query_path" in env["error"]
