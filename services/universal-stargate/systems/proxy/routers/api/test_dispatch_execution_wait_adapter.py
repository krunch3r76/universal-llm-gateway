"""B-middle: execution-id wait adapter for SDK dispatch-link recovery."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.proxy.routers.api.dispatch_bus_recovery import (
    await_bus_closeout_reply,
    recover_execution_from_bus_thread,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict[str, Any]:
        return self._payload


class _RoutingFakeClient:
    def __init__(self, routes: dict[tuple[str, str], list[_FakeResponse]]) -> None:
        self._routes = routes

    async def get(self, path: str, **kwargs: Any) -> _FakeResponse:
        params = kwargs.get("params") or {}
        if path.startswith("/threads/") and path.endswith("/wait"):
            key = ("wait", path.split("/")[2])
        elif path == "/turns/by-number":
            key = ("turn", str(params.get("turn_number")))
        elif path == "/turns":
            key = ("turns", str(params.get("thread")))
        elif path.startswith("/dispatch-links/"):
            key = ("link", path.rsplit("/", 1)[-1])
        else:
            raise AssertionError(f"unexpected GET {path}")
        queue = self._routes.get(key)
        if not queue:
            raise AssertionError(f"no route for {key}")
        return queue.pop(0)


class _FakeClientContext:
    def __init__(self, client: _RoutingFakeClient) -> None:
        self.client = client

    async def __aenter__(self) -> _RoutingFakeClient:
        return self.client

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_recover_blocks_on_wait_and_attaches_closeout_body() -> None:
    closeout_json = '{"status":"complete","summary":"ok"}'
    routes: dict[tuple[str, str], list[_FakeResponse]] = {
        ("link", "exec-wait"): [
            _FakeResponse(
                200,
                {
                    "thread_id": "050",
                    "pipeline_id": "cursor-sdk-generate",
                    "terminal_status": None,
                    "terminal_at": None,
                },
            )
        ],
        ("turns", "050"): [
            _FakeResponse(200, {"turns": []}),
            _FakeResponse(
                200,
                {
                    "turns": [
                        {
                            "from": "cursor-sdk",
                            "subject": "cursor-sdk dispatch complete",
                            "created_at": "2026-06-16T12:00:00+00:00",
                            "turn_number": 2,
                        }
                    ]
                },
            ),
        ],
        ("wait", "050"): [
            _FakeResponse(
                200,
                {"complete": True, "qualifying_reply_turn": 2},
            )
        ],
        ("turn", "2"): [
            _FakeResponse(200, {"body": closeout_json}),
        ],
    }
    client = _RoutingFakeClient(routes)

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(client),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-wait",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
            wait_seconds=5.0,
        )

    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["result"] == closeout_json
    assert recovered["target_thread"] == "050"


@pytest.mark.offline
@pytest.mark.asyncio
async def test_recover_running_without_wait_has_no_result() -> None:
    routes: dict[tuple[str, str], list[_FakeResponse]] = {
        ("link", "exec-no-wait"): [
            _FakeResponse(
                200,
                {
                    "thread_id": "051",
                    "pipeline_id": "cursor-sdk-generate",
                    "terminal_status": None,
                    "terminal_at": None,
                },
            )
        ],
        ("turns", "051"): [_FakeResponse(200, {"turns": []})],
    }

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(_RoutingFakeClient(routes)),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-no-wait",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
            wait_seconds=0.0,
        )

    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["result"] is None


@pytest.mark.offline
@pytest.mark.asyncio
async def test_await_bus_closeout_reply_fetches_turn_body() -> None:
    routes: dict[tuple[str, str], list[_FakeResponse]] = {
        ("wait", "052"): [
            _FakeResponse(200, {"complete": True, "qualifying_reply_turn": 3}),
        ],
        ("turn", "3"): [_FakeResponse(200, {"body": "closeout-body"})],
    }
    client = _RoutingFakeClient(routes)

    body = await await_bus_closeout_reply(
        client,
        thread_id="052",
        wait_seconds=2.0,
        headers={"Authorization": "Bearer test-token"},
    )
    assert body == "closeout-body"


@pytest.mark.offline
def test_get_pipeline_execution_passes_wait_to_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from systems.proxy.dependencies import get_auth_dependency, get_proxy
    from systems.proxy.routers.api import pipelines_dispatch as mod

    tracker = MagicMock()
    tracker.wait_for_terminal = AsyncMock(return_value=None)
    tracker._agent_bus_url = "unix:///tmp/agent-bus.sock"
    tracker._agent_bus_token = "test-token"

    recover_mock = AsyncMock(
        return_value={
            "execution_id": "exec-route-wait",
            "pipeline": "cursor-sdk-generate",
            "status": "completed",
            "result": '{"status":"complete"}',
            "recovered_from": "bus_thread",
            "target_thread": "053",
        }
    )

    monkeypatch.setattr(mod, "_get_tracker", lambda _proxy: tracker)
    monkeypatch.setattr(mod, "fetch_terminal", AsyncMock(return_value=None))
    monkeypatch.setattr(mod, "recover_execution_from_bus_thread", recover_mock)

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[get_proxy] = lambda: MagicMock()
    app.dependency_overrides[get_auth_dependency] = lambda: {}

    response = TestClient(app).get(
        "/pipelines/executions/exec-route-wait",
        params={"wait": 15},
    )
    assert response.status_code == 200, response.text
    recover_mock.assert_awaited_once_with(
        "exec-route-wait",
        url="unix:///tmp/agent-bus.sock",
        auth_token="test-token",
        wait_seconds=15.0,
    )
    assert response.json()["result"] == '{"status":"complete"}'
