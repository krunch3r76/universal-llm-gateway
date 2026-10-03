"""Friction a:37487 — dispatch-thread bus GET error text and one transport retry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.dispatch_thread_bus_read import (
    bus_http_error_preview,
    format_bus_read_exc,
)
from systems.frontier_consult.dispatch_thread_context import (
    read_latest_dispatch_thread_body,
)

pytestmark = pytest.mark.offline

_MAKE_CLIENT = "systems.frontier_consult.dispatch_thread_bus_read.make_async_client"


def _request() -> httpx.Request:
    return httpx.Request("GET", "http://localhost/turns")


def _ok_client(turn: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"turns": [turn]}
    resp.text = ""
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return mock_ctx


def test_format_bus_read_exc_empty_str_names_class() -> None:
    exc = httpx.ReadError("", request=_request())
    assert str(exc) == ""
    assert format_bus_read_exc(exc) == "ReadError"


def test_format_bus_read_exc_keeps_message() -> None:
    exc = httpx.ConnectError("connection refused", request=_request())
    assert "ConnectError" in format_bus_read_exc(exc)
    assert "connection refused" in format_bus_read_exc(exc)


def test_bus_http_error_preview_empty_body() -> None:
    resp = MagicMock()
    resp.text = ""
    assert bus_http_error_preview(resp) == "(empty body)"


@pytest.mark.asyncio
async def test_empty_transport_error_names_class_in_503() -> None:
    """Breaks when str(exc) is empty: callers used to see a trailing colon."""
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(side_effect=httpx.ReadError("", request=_request()))
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch(_MAKE_CLIENT, return_value=mock_ctx):
        with pytest.raises(FrontierEndpointError) as excinfo:
            await read_latest_dispatch_thread_body(
                request_id="req-37487",
                dispatch_thread_id="14736",
                role="cursor-sdk",
            )
    err = excinfo.value
    assert err.code == "dispatch_thread_read_failed"
    assert "ReadError" in err.reason
    assert err.reason.rstrip().endswith("ReadError") or "ReadError:" in err.reason
    assert not err.reason.endswith(": ")


@pytest.mark.asyncio
async def test_transport_error_retries_once_then_admits() -> None:
    """Breaks on a one-shot UDS reset immediately after thread create."""
    fail = MagicMock()
    fail.__aenter__ = AsyncMock(
        side_effect=httpx.ReadError("Connection reset by peer", request=_request())
    )
    fail.__aexit__ = AsyncMock(return_value=None)
    ok = _ok_client(
        {
            "from": "web-anthropic",
            "to": "cursor-sdk",
            "body": "TYPE: DIRECTIVE\nbrief",
            "turn_number": 1,
        }
    )
    with patch(_MAKE_CLIENT, side_effect=[fail, ok]):
        text = await read_latest_dispatch_thread_body(
            request_id="req-37487-retry",
            dispatch_thread_id="14736",
            role="cursor-sdk",
        )
    assert "TYPE: DIRECTIVE" in text
