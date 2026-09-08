"""Hermetic tests for team_dispatch op=steer → GIW park relay (Leg E / AC-1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import Response

from systems.frontier_consult.cursor_sdk_steer_dispatch import steer_park_for_restart
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
