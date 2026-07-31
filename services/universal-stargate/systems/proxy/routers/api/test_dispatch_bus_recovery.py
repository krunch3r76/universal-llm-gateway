"""Tests for tracker-miss recovery via durable dispatch links."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from systems.proxy.routers.api.dispatch_bus_recovery import (
    recover_execution_from_bus_thread,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses

    async def get(self, path: str, **kwargs: Any) -> _FakeResponse:
        if not self._responses:
            raise AssertionError(f"unexpected GET {path}")
        return self._responses.pop(0)


class _FakeClientContext:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.client = _FakeClient(responses)

    async def __aenter__(self) -> _FakeClient:
        return self.client

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_recover_from_terminal_dispatch_link() -> None:
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "042",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": "completed",
                "terminal_at": "2026-06-15T12:00:00+00:00",
            },
        ),
        _FakeResponse(200, {"turns": []}),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-recover",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["execution_id"] == "exec-recover"
    assert recovered["status"] == "completed"
    assert recovered["target_thread"] == "042"
    assert recovered["recovered_from"] == "bus_thread"


@pytest.mark.asyncio
async def test_recover_from_closeout_turn_when_link_not_terminal() -> None:
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "043",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
            },
        ),
        _FakeResponse(
            200,
            {
                "turns": [
                    {
                        "from": "cursor-sdk",
                        "subject": "cursor-sdk dispatch complete",
                        "created_at": "2026-06-15T12:05:00+00:00",
                    }
                ]
            },
        ),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-closeout",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["completed_at"] == "2026-06-15T12:05:00+00:00"


@pytest.mark.asyncio
async def test_recover_returns_none_when_link_missing() -> None:
    responses = [_FakeResponse(404)]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "missing-exec",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is None


@pytest.mark.asyncio
async def test_resolver_running_when_link_present_nonterminal() -> None:
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "045",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
            },
        ),
        _FakeResponse(200, {"turns": []}),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-running",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["execution_id"] == "exec-running"
    assert recovered["target_thread"] == "045"
    assert recovered["completed_at"] is None


@pytest.mark.asyncio
async def test_resolver_unknown_link_still_404() -> None:
    responses = [_FakeResponse(404)]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "unknown-exec",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is None


@pytest.mark.asyncio
async def test_resolver_link_transport_error_no_false_terminal() -> None:
    import httpx

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        side_effect=httpx.HTTPError("connection refused"),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-transport",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is None


@pytest.mark.asyncio
async def test_resolver_turns_fetch_non_200_no_false_running_or_terminal() -> None:
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "046",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
            },
        ),
        _FakeResponse(503),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-turns-fail",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is None


@pytest.mark.asyncio
async def test_recover_returns_none_when_no_auth_token() -> None:
    recovered = await recover_execution_from_bus_thread(
        "exec-no-auth",
        url="unix:///tmp/agent-bus.sock",
        auth_token="",
    )
    assert recovered is None


@pytest.mark.asyncio
async def test_recover_returns_none_when_link_non_200() -> None:
    responses = [_FakeResponse(503)]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-link-error",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is None


def test_get_pipeline_execution_returns_recovered_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from systems.proxy.dependencies import get_auth_dependency, get_proxy
    from systems.proxy.routers.api import pipelines_dispatch as mod

    tracker = MagicMock()
    tracker.wait_for_terminal = AsyncMock(return_value=None)
    tracker._agent_bus_url = "unix:///tmp/agent-bus.sock"
    tracker._agent_bus_token = "test-token"

    monkeypatch.setattr(mod, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(mod, "fetch_terminal", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mod,
        "recover_execution_from_bus_thread",
        AsyncMock(
            return_value={
                "execution_id": "exec-route",
                "pipeline": "cursor-sdk-generate",
                "status": "completed",
                "recovered_from": "bus_thread",
                "target_thread": "044",
            }
        ),
    )

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_proxy] = lambda: MagicMock()
    app.dependency_overrides[get_auth_dependency] = lambda: {}

    response = TestClient(app).get("/pipelines/executions/exec-route")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recovered_from"] == "bus_thread"
    assert body["status"] == "completed"


def test_get_pipeline_execution_still_404_when_no_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from systems.proxy.dependencies import get_auth_dependency, get_proxy
    from systems.proxy.routers.api import pipelines_dispatch as mod

    tracker = MagicMock()
    tracker.wait_for_terminal = AsyncMock(return_value=None)
    tracker._agent_bus_url = "unix:///tmp/agent-bus.sock"
    tracker._agent_bus_token = "test-token"

    monkeypatch.setattr(mod, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(mod, "fetch_terminal", AsyncMock(return_value=None))
    monkeypatch.setattr(
        mod, "recover_execution_from_bus_thread", AsyncMock(return_value=None)
    )

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_proxy] = lambda: MagicMock()
    app.dependency_overrides[get_auth_dependency] = lambda: {}

    response = TestClient(app).get("/pipelines/executions/unknown-exec")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "execution_id_expired_or_unknown"
