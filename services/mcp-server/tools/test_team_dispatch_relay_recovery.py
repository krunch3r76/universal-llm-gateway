"""MCP team_dispatch relay transport codes after Stargate response loss (a:38605)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tools._team_dispatch_relay_recovery import (
    build_admit_pending_response,
    build_response_lost_error,
    is_admit_pending_transport,
    transport_error_code,
)
from tools.frontier import _relay, register_frontier_tools

pytestmark = pytest.mark.offline

_COORD = "15601"


def test_timeout_maps_to_response_lost_not_unreachable() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    assert is_admit_pending_transport(exc)
    assert transport_error_code(exc) == "stargate_response_lost"


def test_remote_protocol_error_maps_to_response_lost() -> None:
    exc = httpx.RemoteProtocolError(
        "peer closed connection",
        request=httpx.Request("POST", "http://stargate/"),
    )
    assert is_admit_pending_transport(exc)
    assert transport_error_code(exc) == "stargate_response_lost"


def test_connect_error_stays_unreachable() -> None:
    exc = httpx.ConnectError("refused", request=httpx.Request("POST", "http://stargate/"))
    assert transport_error_code(exc) == "stargate_unreachable"


def test_response_lost_includes_dispatch_thread_and_fix_hint() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    payload = build_admit_pending_response(
        exc=exc,
        dispatch_id="did-test-1",
        work_key="todo:wk",
        dispatch_thread_id=_COORD,
    )
    assert payload["status"] == "admit_pending"
    assert payload["dispatch_id"] == "did-test-1"
    assert payload["work_key"] == "todo:wk"
    assert payload["do_not_refire"] is True
    assert payload["poll_after_s"] == 30
    assert "error" not in payload
    joined = str(payload).lower()
    assert "admitted" not in joined.replace("admit_pending", "")
    lost = build_response_lost_error(exc=exc, dispatch_thread_id=_COORD)
    err = lost["error"]
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
        "work_key": "todo:relay-timeout",
        "dispatch_id": "did-relay-timeout",
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["status"] == "admit_pending"
    assert result["dispatch_id"] == "did-relay-timeout"
    assert result["work_key"] == "todo:relay-timeout"
    assert "error" not in result
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
        "work_key": "todo:relay-rpe",
        "dispatch_id": "did-relay-rpe",
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["status"] == "admit_pending"
    assert result["dispatch_id"] == "did-relay-rpe"
    assert "error" not in result


@pytest.mark.asyncio
async def test_relay_generate_read_timeout_admit_pending_preserves_body_dispatch_id() -> None:
    captured_body: dict = {}
    stargate_client = MagicMock()

    async def _post(_endpoint: str, *, json: dict | None = None, **_kwargs: object) -> None:
        if json:
            captured_body.update(json)
        raise httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))

    stargate_client.post = _post
    stargate_ctx = MagicMock()
    stargate_ctx.__aenter__ = AsyncMock(return_value=stargate_client)
    stargate_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "dispatch_thread_id": _COORD,
        "work_key": "todo:capture-did",
        "dispatch_id": "did-caller-supplied",
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert captured_body["dispatch_id"] == "did-caller-supplied"
    assert result["status"] == "admit_pending"
    assert result["dispatch_id"] == captured_body["dispatch_id"]
    assert "error" not in result


class _ToolNameRecorder:
    def __init__(self) -> None:
        self.functions: dict[str, Any] = {}

    def tool(self, **_kwargs: Any) -> Any:
        def decorator(fn: Any) -> Any:
            self.functions[fn.__name__] = fn
            return fn

        return decorator


def test_cdp_generate_relay_body_omits_dispatch_id() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record"),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                model="cdp/opus-5.5",
                contract="delivery-review",
                dispatch_thread_id=_COORD,
            )
        )

    assert len(relay_calls) == 1
    assert "dispatch_id" not in relay_calls[0]["body"]


def test_cursor_sdk_generate_relay_stamps_caller_dispatch_id() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record"),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="freeform",
                dispatch_thread_id=_COORD,
                prompt="relay dispatch_id stamp test",
                dispatch_id="did-caller-41543",
                lane="B",
            )
        )

    assert relay_calls[0]["body"]["dispatch_id"] == "did-caller-41543"


def test_cursor_sdk_generate_mints_dispatch_id_when_caller_omits() -> None:
    recorder = _ToolNameRecorder()
    register_frontier_tools(recorder)
    team_dispatch_fn = recorder.functions["team_dispatch"]
    relay_calls: list[dict[str, Any]] = []

    async def _fake_relay(
        *, endpoint: str, body: dict[str, Any], record_prefix: str
    ) -> dict[str, Any]:
        relay_calls.append({"endpoint": endpoint, "body": body})
        return {"execution_id": "exec-test", "thread_id": "thread-test"}

    fixed = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    with (
        patch("tools.frontier._relay", side_effect=_fake_relay),
        patch("tools.frontier.record"),
        patch("tools.frontier.uuid.uuid4", return_value=fixed),
    ):
        asyncio.run(
            team_dispatch_fn(
                op="generate",
                seat="cursor-sdk",
                contract="freeform",
                dispatch_thread_id=_COORD,
                prompt="relay dispatch_id mint test",
                lane="B",
            )
        )

    assert relay_calls[0]["body"]["dispatch_id"] == str(fixed)


@pytest.mark.asyncio
async def test_cdp_generate_read_timeout_returns_response_lost_not_admit_pending() -> None:
    stargate_client = MagicMock()
    stargate_client.post = AsyncMock(
        side_effect=httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    )
    stargate_ctx = MagicMock()
    stargate_ctx.__aenter__ = AsyncMock(return_value=stargate_client)
    stargate_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "model": "cdp/opus-5.5",
        "dispatch_thread_id": _COORD,
        "job": "delivery-review",
    }

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["error"]["code"] == "stargate_response_lost"
    assert "admit_pending" not in result
