"""Hermetic tests for team_dispatch op=steer → GIW park relay (Leg E / AC-1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import Response

from systems.frontier_consult.cursor_sdk_steer_dispatch import (
    steer_inject_directive,
    steer_park_for_restart,
)
from systems.frontier_consult.route import TeamDispatchSteerBody, team_dispatch


class _FakeHttpxResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_park_for_restart_posts_giw_park_route() -> None:
    captured: dict[str, Any] = {}

    class _Client:
        async def post(self, path: str, json: dict[str, Any]) -> _FakeHttpxResponse:
            captured["path"] = path
            captured["json"] = json
            return _FakeHttpxResponse(
                202,
                {
                    "dispatch_id": "disp-steer-1",
                    "park_state": "park_requested",
                },
            )

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        return_value=_Client(),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        ok, detail = await steer_park_for_restart(
            request_id="req-steer",
            dispatch_id="disp-steer-1",
            reason="free lane for lead",
            actor="cursor",
        )

    assert ok is True
    assert captured["path"] == "/api/v1/cursor/dispatch/disp-steer-1/park"
    assert captured["json"] == {
        "reason": "free lane for lead",
        "actor": "cursor",
        "mode": "cancel",
    }
    assert detail["steer"] == "park_for_restart"
    assert detail["dispatch_id"] == "disp-steer-1"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_park_propagates_giw_refusal() -> None:
    class _Client:
        async def post(self, path: str, json: dict[str, Any]) -> _FakeHttpxResponse:
            return _FakeHttpxResponse(
                409,
                {
                    "code": "CURSOR_PARK_SUPERSEDE_IN_FLIGHT",
                    "message": "supersede owns interrupt",
                    "source": "git_integration_worker",
                    "retryable": False,
                    "data": {"dispatch_id": "disp-busy"},
                },
            )

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        return_value=_Client(),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        ok, detail = await steer_park_for_restart(
            request_id="req-refuse",
            dispatch_id="disp-busy",
            reason="operator recycle",
        )

    assert ok is False
    assert detail["http_status"] == 409
    assert detail["code"] == "CURSOR_PARK_SUPERSEDE_IN_FLIGHT"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_team_dispatch_steer_route_returns_giw_body() -> None:
    body = TeamDispatchSteerBody(
        op="steer",
        dispatch_id="disp-route-1",
        steer="park_for_restart",
        reason="hand lane to lead",
        actor="cursor",
    )
    with patch(
        "systems.frontier_consult.route.steer_park_for_restart",
        new=AsyncMock(
            return_value=(
                True,
                {
                    "status_code": 202,
                    "dispatch_id": "disp-route-1",
                    "park_state": "park_requested",
                },
            )
        ),
    ) as steer_mock:
        result = await team_dispatch(body, Response())

    steer_mock.assert_awaited_once()
    assert result == {
        "status_code": 202,
        "dispatch_id": "disp-route-1",
        "park_state": "park_requested",
    }


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_park_transport_error_fail_closed() -> None:
    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        side_effect=httpx.ConnectError("connection refused"),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        ok, detail = await steer_park_for_restart(
            request_id="req-transport",
            dispatch_id="disp-x",
            reason="test",
        )

    assert ok is False
    assert detail["failure_layer"] == "transport"
    assert detail["code"] == "CURSOR_WORKER_UNREACHABLE"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_inject_posts_giw_inject_route() -> None:
    captured: dict[str, Any] = {}

    class _Client:
        async def post(self, path: str, json: dict[str, Any]) -> _FakeHttpxResponse:
            captured["path"] = path
            captured["json"] = json
            return _FakeHttpxResponse(
                202,
                {
                    "dispatch_id": "disp-inject-1",
                    "execution_id": "exec-inject-1",
                    "steer": "inject",
                    "inject_state": "pending",
                    "entry_id": "e1",
                    "authority_turn_id": "42",
                },
            )

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        return_value=_Client(),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        ok, detail = await steer_inject_directive(
            request_id="req-inject",
            dispatch_id="disp-inject-1",
            directive="check logs",
            reason="operator steer",
            actor="cursor",
            ttl_s=120,
        )

    assert ok is True
    assert captured["path"] == "/api/v1/cursor/dispatch/disp-inject-1/inject"
    assert captured["json"] == {
        "directive": "check logs",
        "reason": "operator steer",
        "actor": "cursor",
        "ttl_s": 120,
    }
    assert detail["steer"] == "inject"
    assert detail["inject_state"] == "pending"
    assert "park_kind" not in detail


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_inject_propagates_giw_refusal() -> None:
    class _Client:
        async def post(self, path: str, json: dict[str, Any]) -> _FakeHttpxResponse:
            return _FakeHttpxResponse(
                404,
                {
                    "code": "CURSOR_INJECT_NOT_FOUND",
                    "message": "inject refused: NOT_FOUND",
                    "source": "git_integration_worker",
                    "retryable": False,
                    "data": {"dispatch_id": "disp-missing"},
                },
            )

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        return_value=_Client(),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        ok, detail = await steer_inject_directive(
            request_id="req-refuse-inject",
            dispatch_id="disp-missing",
            directive="nudge",
            reason="test",
        )

    assert ok is False
    assert detail["http_status"] == 404
    assert detail["code"] == "CURSOR_INJECT_NOT_FOUND"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_team_dispatch_steer_inject_route_returns_giw_body() -> None:
    body = TeamDispatchSteerBody(
        op="steer",
        dispatch_id="disp-route-inject",
        steer="inject",
        reason="mid-hop nudge",
        directive="verify Stargate route",
        actor="cursor",
    )
    with patch(
        "systems.frontier_consult.route.steer_inject_directive",
        new=AsyncMock(
            return_value=(
                True,
                {
                    "status_code": 202,
                    "dispatch_id": "disp-route-inject",
                    "execution_id": "exec-route-inject",
                    "inject_state": "pending",
                },
            )
        ),
    ) as steer_mock:
        result = await team_dispatch(body, Response())

    steer_mock.assert_awaited_once()
    assert result == {
        "status_code": 202,
        "dispatch_id": "disp-route-inject",
        "execution_id": "exec-route-inject",
        "inject_state": "pending",
    }


@pytest.mark.offline
@pytest.mark.asyncio
async def test_team_dispatch_steer_inject_missing_directive_422() -> None:
    with pytest.raises(ValueError, match="directive is required"):
        TeamDispatchSteerBody(
            op="steer",
            dispatch_id="disp-x",
            steer="inject",
            reason="missing directive",
        )


@pytest.mark.offline
@pytest.mark.asyncio
async def test_steer_routes_key_on_dispatch_id_not_execution_id() -> None:
    """AC4 — relay paths use dispatch_id only."""
    paths: list[str] = []

    class _Client:
        async def post(self, path: str, json: dict[str, Any]) -> _FakeHttpxResponse:
            paths.append(path)
            return _FakeHttpxResponse(202, {"dispatch_id": "disp-key", "steer": "inject"})

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

    with patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.make_async_client",
        return_value=_Client(),
    ), patch(
        "systems.frontier_consult.cursor_sdk_steer_dispatch.worker_base_url",
        return_value="http://giw.test",
    ):
        await steer_inject_directive(
            request_id="req-key",
            dispatch_id="disp-key",
            directive="nudge",
            reason="ac4",
        )

    assert paths == ["/api/v1/cursor/dispatch/disp-key/inject"]
    assert all("/execution/" not in p for p in paths)
