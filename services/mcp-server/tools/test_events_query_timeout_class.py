"""Acceptance tests for MCP observability client timeout classes (spec S4b item 7)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from event_store.query_client_errors import (
    ERROR_CLASS_CLIENT_DEADLINE,
    ERROR_CLASS_LOCK_WAIT,
)

from tools.events import _query_event_service


def _mock_client(*, post_side_effect=None, post_return=None) -> MagicMock:
    mock_client = MagicMock()
    if post_side_effect is not None:
        mock_client.post.side_effect = post_side_effect
    else:
        mock_client.post.return_value = post_return
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_client)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_ctx


def test_ac7_read_timeout_returns_client_deadline() -> None:
    mock_ctx = _mock_client(post_side_effect=httpx.ReadTimeout("read timed out"))
    with patch("tools.events.make_sync_client", return_value=mock_ctx):
        result = _query_event_service({"type": "operations"})
    assert result["error_class"] == ERROR_CLASS_CLIENT_DEADLINE
    assert "10s client deadline" in result["error"]
    assert "query_completed_age_ms" in result["error"]


def test_ac7_lock_wait_envelope_from_503() -> None:
    request = httpx.Request("POST", "http://localhost/v1/query")
    response = httpx.Response(
        503,
        json={
            "error": "Event store waited on a database lock: database is locked",
            "error_class": ERROR_CLASS_LOCK_WAIT,
        },
        request=request,
    )
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "busy", request=request, response=response
    )
    mock_ctx = _mock_client(post_return=mock_response)
    with patch("tools.events.make_sync_client", return_value=mock_ctx):
        result = _query_event_service({"type": "operations"})
    assert result["error_class"] == ERROR_CLASS_LOCK_WAIT
    assert "wait" in result["error"].lower()


def test_ac7_other_http_status_keeps_generic_error() -> None:
    request = httpx.Request("POST", "http://localhost/v1/query")
    response = httpx.Response(500, text="internal", request=request)
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "internal"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "err", request=request, response=response
    )
    mock_ctx = _mock_client(post_return=mock_response)
    with patch("tools.events.make_sync_client", return_value=mock_ctx):
        result = _query_event_service({"type": "operations"})
    assert "error" in result
    assert result.get("error_class") != ERROR_CLASS_LOCK_WAIT
