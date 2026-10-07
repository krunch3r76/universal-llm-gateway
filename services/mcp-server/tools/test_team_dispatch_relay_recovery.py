"""MCP team_dispatch relay recovery after Stargate transport loss (a:38605)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tools._team_dispatch_relay_recovery import (
    build_response_lost_error,
    execution_id_from_turn,
    recover_team_dispatch_after_transport_error,
    request_id_from_generate_subject,
    transport_error_code,
    worker_thread_from_admit_body,
)
from tools.frontier import _relay

pytestmark = pytest.mark.offline

_EXEC = "72b588fe-13bb-40b7-b10b-d2f01884a4dc"
_COORD = "15601"
_WORKER = "15602"


def test_worker_thread_from_admit_body_parses_backticks() -> None:
    body = (
        f"Worker thread `{_WORKER}` — poll via `poll_hint` from the 202 "
        "response (not this coordination thread)."
    )
    assert worker_thread_from_admit_body(body) == _WORKER


def test_execution_id_from_turn_reads_to_agent() -> None:
    turn = {"to": f"cursor-sdk:dispatch:{_EXEC}"}
    assert execution_id_from_turn(turn) == _EXEC


def test_request_id_from_generate_subject() -> None:
    subject = f"cursor-sdk generate — {_EXEC}"
    assert request_id_from_generate_subject(subject) == _EXEC


def test_timeout_maps_to_response_lost_not_unreachable() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    assert transport_error_code(exc, recovered=False) == "stargate_response_lost"


def test_connect_error_stays_unreachable() -> None:
    exc = httpx.ConnectError("refused", request=httpx.Request("POST", "http://stargate/"))
    assert transport_error_code(exc, recovered=False) == "stargate_unreachable"


def test_response_lost_includes_dispatch_thread_and_fix_hint() -> None:
    exc = httpx.ReadTimeout("", request=httpx.Request("POST", "http://stargate/"))
    payload = build_response_lost_error(exc=exc, dispatch_thread_id=_COORD)
    err = payload["error"]
    assert err["code"] == "stargate_response_lost"
    assert err["dispatch_thread_id"] == _COORD
    assert "cursor-sdk generate admitted" in err["fix_hint"]


@pytest.mark.asyncio
async def test_recovery_builds_admit_envelope_from_bus() -> None:
    coord_turn = {
        "subject": "cursor-sdk generate admitted",
        "body": f"Worker thread `{_WORKER}` — poll via `poll_hint` from the 202 response.",
    }
    worker_turn = {
        "subject": f"cursor-sdk generate — {_EXEC}",
        "to": f"cursor-sdk:dispatch:{_EXEC}",
    }

    async def fake_get(path: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        if f"thread={_COORD}" in path:
            resp.json.return_value = {"turns": [coord_turn]}
        elif f"thread={_WORKER}" in path:
            resp.json.return_value = {"turns": [worker_turn]}
        else:
            resp.json.return_value = {"turns": []}
        return resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    body = {
        "op": "generate",
        "seat": "cursor-sdk",
        "dispatch_thread_id": _COORD,
        "job": "freeform",
    }
    with patch(
        "tools._team_dispatch_relay_recovery.make_async_client",
        return_value=mock_ctx,
    ):
        recovered = await recover_team_dispatch_after_transport_error(
            body=body,
            exc=httpx.ReadTimeout(""),
        )

    assert recovered is not None
    assert recovered["execution_id"] == _EXEC
    assert recovered["thread_id"] == _WORKER
    assert recovered["poll_hint"]["tool"] == "wait"
    assert recovered["recovered_after_relay_transport_error"] is True


@pytest.mark.asyncio
async def test_relay_returns_recovered_envelope_on_read_timeout() -> None:
    coord_turn = {
        "subject": "cursor-sdk generate admitted",
        "body": f"Worker thread `{_WORKER}` — poll via `poll_hint` from the 202 response.",
    }
    worker_turn = {
        "subject": f"cursor-sdk generate — {_EXEC}",
        "to": f"cursor-sdk:dispatch:{_EXEC}",
    }

    async def fake_get(path: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        if f"thread={_COORD}" in path:
            resp.json.return_value = {"turns": [coord_turn]}
        else:
            resp.json.return_value = {"turns": [worker_turn]}
        return resp

    bus_client = MagicMock()
    bus_client.get = AsyncMock(side_effect=fake_get)
    bus_ctx = MagicMock()
    bus_ctx.__aenter__ = AsyncMock(return_value=bus_client)
    bus_ctx.__aexit__ = AsyncMock(return_value=None)

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

    def client_factory(url: str, **kwargs: Any) -> MagicMock:
        if "agent" in url or url.endswith(".sock") or "bus" in url:
            return bus_ctx
        return stargate_ctx

    with patch("tools.frontier.make_async_client", side_effect=client_factory), patch(
        "tools._team_dispatch_relay_recovery.make_async_client",
        return_value=bus_ctx,
    ):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result.get("execution_id") == _EXEC
    assert "error" not in result


@pytest.mark.asyncio
async def test_relay_read_timeout_without_bus_admit_returns_response_lost() -> None:
    bus_client = MagicMock()
    bus_client.get = AsyncMock(
        return_value=MagicMock(status_code=200, json=lambda: {"turns": []})
    )
    bus_ctx = MagicMock()
    bus_ctx.__aenter__ = AsyncMock(return_value=bus_client)
    bus_ctx.__aexit__ = AsyncMock(return_value=None)

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

    with patch("tools.frontier.make_async_client", return_value=stargate_ctx), patch(
        "tools._team_dispatch_relay_recovery.make_async_client",
        return_value=bus_ctx,
    ):
        result = await _relay(
            endpoint="/api/v1/team/dispatch",
            body=body,
            record_prefix="mcp.team.dispatch",
        )

    assert result["error"]["code"] == "stargate_response_lost"
    assert result["error"]["dispatch_thread_id"] == _COORD
