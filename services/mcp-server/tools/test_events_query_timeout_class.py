"""Acceptance tests for MCP observability client timeout classes."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from tools.events import _query_event_service


class _Client:
    def __init__(self, action) -> None:  # type: ignore[no-untyped-def]
        self._action = action

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def get(self, *_a: object, **_k: object) -> httpx.Response:
        return self._action()


def test_ac7_read_timeout_returns_client_deadline() -> None:
    def action() -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    with patch(
        "event_store.query_client.make_sync_client", return_value=_Client(action)
    ):
        result = _query_event_service("operations")
    assert result["error_class"] == "client_deadline"
    assert "10s client deadline" in result["error"]
    assert "query_completed_age_ms" in result["error"]


def test_ac7_lock_wait_envelope_from_503() -> None:
    def action() -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "code": "LOCK_WAIT",
                "message": "Event store waited on a database lock.",
                "source": "rpc",
                "retryable": True,
                "data": {"error_class": "lock_wait"},
            },
            request=httpx.Request("GET", "http://localhost/api/v1/observability"),
        )

    with patch(
        "event_store.query_client.make_sync_client", return_value=_Client(action)
    ):
        result = _query_event_service("operations")
    assert result["error_class"] == "lock_wait"
    assert "wait" in result["error"].lower()


def test_ac7_other_http_status_keeps_generic_error() -> None:
    def action() -> httpx.Response:
        return httpx.Response(
            500,
            json={"code": "OPERATION_FAILED", "message": "internal"},
            request=httpx.Request("GET", "http://localhost/api/v1/observability"),
        )

    with patch(
        "event_store.query_client.make_sync_client", return_value=_Client(action)
    ):
        result = _query_event_service("operations")
    assert "error" in result
    assert result.get("error_class") != "lock_wait"
