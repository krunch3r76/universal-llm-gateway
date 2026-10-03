"""Tests for tracker-miss recovery via durable dispatch links."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_bus_store.sdk_liveness import LivenessVerdict
from fastapi.testclient import TestClient

from systems.proxy.routers.api.dispatch_bus_recovery import (
    recover_execution_from_bus_thread,
)


def _fresh_linked_at() -> str:
    return (datetime.now(UTC) - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
async def test_recover_running_when_only_parked_turn_present() -> None:
    """AC-SR-12: PARKED must not terminalize execution recovery."""
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "048",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
                "linked_at": _fresh_linked_at(),
            },
        ),
        _FakeResponse(
            200,
            {
                "turns": [
                    {
                        "from": "cursor-sdk",
                        "subject": (
                            "cursor-sdk dispatch disp-park PARKED "
                            "(for GIW restart intent-1)"
                        ),
                        "created_at": "2026-06-16T12:01:00+00:00",
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
            "exec-parked-only",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["completed_at"] is None


@pytest.mark.asyncio
async def test_recover_completes_on_child_closeout_after_parked() -> None:
    """AC-SR-12: child CLOSEOUT under inherited execution_id completes wait."""
    closeout_json = '{"status":"complete","summary":"resumed work"}'
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "049",
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
                        "subject": "cursor-sdk dispatch child-1 COMPLETED",
                        "created_at": "2026-06-16T12:10:00+00:00",
                        "turn_number": 5,
                        "body": closeout_json,
                    },
                    {
                        "from": "cursor-sdk",
                        "subject": (
                            "cursor-sdk dispatch parent-1 PARKED "
                            "(for GIW restart intent-1)"
                        ),
                        "created_at": "2026-06-16T12:05:00+00:00",
                        "turn_number": 4,
                    },
                ]
            },
        ),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "exec-park-resume",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "completed"
    assert recovered["result"] == closeout_json


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
                "linked_at": _fresh_linked_at(),
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
async def test_resolver_unknown_when_stream_died_without_terminal_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale non-terminal link is not recovered as running (a:36832)."""
    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.DEFER, "probe_unreachable:test", None),
    )
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "045",
                "pipeline_id": "cdp-generate",
                "terminal_status": None,
                "terminal_at": None,
                "delivery_at": None,
                "linked_at": "2026-09-22T02:44:02Z",
            },
        ),
        _FakeResponse(200, {"turns": []}),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "96f5f3f0-2867-477a-99e5-d79573d77d1e",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "unknown"
    assert recovered["liveness_reason"] == "no_liveness_signal"
    assert recovered["completed_at"] is None


@pytest.mark.asyncio
async def test_recover_running_when_past_grace_skip_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past grace and SKIP_LIVE recover as running, with no terminal write."""
    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.SKIP_LIVE, "worker_live", None),
    )
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "045",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
                "delivery_at": None,
                "linked_at": "2026-09-22T02:44:02Z",
            },
        ),
        _FakeResponse(200, {"turns": []}),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "running"
    assert recovered["completed_at"] is None
    assert "liveness_reason" not in recovered
    assert "terminal_status" not in recovered


@pytest.mark.asyncio
async def test_recover_unknown_when_past_grace_heartbeat_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past grace and heartbeat_stale recover as unknown / stream_dead_no_terminal."""
    monkeypatch.setattr(
        "agent_bus_store.sdk_liveness.evaluate_link_liveness",
        lambda **_kwargs: (LivenessVerdict.ALLOW_ORPHAN, "heartbeat_stale", None),
    )
    responses = [
        _FakeResponse(
            200,
            {
                "thread_id": "045",
                "pipeline_id": "cursor-sdk-generate",
                "terminal_status": None,
                "terminal_at": None,
                "delivery_at": None,
                "linked_at": "2026-09-22T02:44:02Z",
            },
        ),
        _FakeResponse(200, {"turns": []}),
    ]

    with patch(
        "systems.proxy.routers.api.dispatch_bus_recovery.make_async_client",
        return_value=_FakeClientContext(responses),
    ):
        recovered = await recover_execution_from_bus_thread(
            "67aae3ee-ff0b-4e63-bfad-de87ceac2f93",
            url="unix:///tmp/agent-bus.sock",
            auth_token="test-token",
        )

    assert recovered is not None
    assert recovered["status"] == "unknown"
    assert recovered["liveness_reason"] == "stream_dead_no_terminal"
    assert recovered["completed_at"] is None
    assert "terminal_status" not in recovered


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
    from systems.proxy.routers.api import executions as mod

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

    response = TestClient(app).get("/executions/exec-route")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recovered_from"] == "bus_thread"
    assert body["status"] == "completed"


def test_get_pipeline_execution_still_404_when_no_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    from systems.proxy.dependencies import get_auth_dependency, get_proxy
    from systems.proxy.routers.api import executions as mod

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

    response = TestClient(app).get("/executions/unknown-exec")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "execution_id_expired_or_unknown"
