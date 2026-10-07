"""MCP team_dispatch relay transport codes after Stargate response loss (a:38605)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tools._team_dispatch_relay_recovery import (
    build_response_lost_error,
    transport_error_code,
)
from tools.frontier import _relay

pytestmark = pytest.mark.offline

_COORD = "15601"


def test_timeout_maps_to_response_lost_not_unreachable() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    assert transport_error_code(exc) == "stargate_response_lost"


def test_remote_protocol_error_maps_to_response_lost() -> None:
    exc = httpx.RemoteProtocolError(
        "peer closed connection",
        request=httpx.Request("POST", "http://stargate/"),
    )
    assert transport_error_code(exc) == "stargate_response_lost"


def test_connect_error_stays_unreachable() -> None:
    exc = httpx.ConnectError("refused", request=httpx.Request("POST", "http://stargate/"))
    assert transport_error_code(exc) == "stargate_unreachable"


def test_response_lost_includes_dispatch_thread_and_fix_hint() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    payload = build_response_lost_error(exc=exc, dispatch_thread_id=_COORD)
    err = payload["error"]
    assert err["code"] == "stargate_response_lost"
    assert err["dispatch_thread_id"] == _COORD
    assert "newest-first" in err["fix_hint"]
    assert "cursor-sdk generate" in err["fix_hint"]


@pytest.mark.asyncio
async def test_relay_read_timeout_returns_response_lost_not_unreachable() -> None:
    stargate_client = MagicMock()
    stargate_client.post = AsyncMock(
        side_effect=httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    )
    stargate_ctx = MagicMock()
    stargate_ctx.__aenter__ = AsyncMock(return_value=stargate_client)
    stargate_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "dispatch_thread_id": _COORD,
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["error"]["code"] == "stargate_response_lost"
    assert result["error"]["dispatch_thread_id"] == _COORD
    assert "stargate_unreachable" not in str(result)


@pytest.mark.asyncio
async def test_relay_connect_error_stays_unreachable_without_bus_scrape() -> None:
    stargate_client = MagicMock()
    stargate_client.post = AsyncMock(
        side_effect=httpx.ConnectError(
            "connection refused", request=httpx.Request("POST", "http://stargate/")
        )
    )
    stargate_ctx = MagicMock()
    stargate_ctx.__aenter__ = AsyncMock(return_value=stargate_client)
    stargate_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "dispatch_thread_id": _COORD,
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["error"]["code"] == "stargate_unreachable"


@pytest.mark.asyncio
async def test_relay_remote_protocol_error_returns_response_lost() -> None:
    stargate_client = MagicMock()
    stargate_client.post = AsyncMock(
        side_effect=httpx.RemoteProtocolError(
            "Server disconnected",
            request=httpx.Request("POST", "http://stargate/"),
        )
    )
    stargate_ctx = MagicMock()
    stargate_ctx.__aenter__ = AsyncMock(return_value=stargate_client)
    stargate_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "dispatch_thread_id": _COORD,
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["error"]["code"] == "stargate_response_lost"
