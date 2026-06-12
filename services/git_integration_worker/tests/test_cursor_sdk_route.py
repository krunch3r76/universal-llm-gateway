"""Unit tests for cursor-sdk dispatch route, registry, and bus client."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
)
from services.git_integration_worker.cursor_home import CursorHomeConfigError
from services.git_integration_worker.cursor_sdk_context import CursorSdkParityError
from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _mock_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _dispatch_body(**overrides: Any) -> dict[str, Any]:
    base = {
        "thread_id": "1558",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "message": "hello",
    }
    base.update(overrides)
    return base


def test_dispatch_untrusted_model_422(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(model="cursor/unknown-model"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_MODEL_UNTRUSTED"
    assert resp.json()["source"] == "gateway"


def test_dispatch_packet_traversal_reject(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(message=None, packet_path="../etc/passwd"),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_PACKET_INVALID"


@patch(
    "services.git_integration_worker.routes.cursor_sdk.asyncio.create_task",
    return_value=MagicMock(done=lambda: False),
)
def test_dispatch_parity_failure_422(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_parity(_repo: object) -> dict[str, object]:
        raise CursorSdkParityError("MCP token not configured")

    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_sdk.validate_dispatch_context",
        _raise_parity,
    )
    resp = client.post("/api/v1/cursor/dispatch", json=_dispatch_body())
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_SDK_PARITY"


@patch(
    "services.git_integration_worker.routes.cursor_sdk.asyncio.create_task",
    return_value=MagicMock(done=lambda: False),
)
def test_dispatch_admits_and_spawns_task(
    _mock_task: MagicMock, client: TestClient
) -> None:
    resp = client.post("/api/v1/cursor/dispatch", json=_dispatch_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["admitted"] is True
    assert body["dispatch_id"] == "disp-1"
    assert body["model_id"] == "composer-2.5"


@patch(
    "services.git_integration_worker.routes.cursor_sdk.asyncio.create_task",
    return_value=MagicMock(done=lambda: False),
)
def test_dispatch_idempotent_hit(_mock_task: MagicMock, client: TestClient) -> None:
    payload = _dispatch_body()
    first = client.post("/api/v1/cursor/dispatch", json=payload)
    second = client.post("/api/v1/cursor/dispatch", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()


@patch(
    "services.git_integration_worker.routes.cursor_sdk.asyncio.create_task",
    return_value=MagicMock(done=lambda: False),
)
def test_dispatch_conflict_409(_mock_task: MagicMock, client: TestClient) -> None:
    client.post("/api/v1/cursor/dispatch", json=_dispatch_body())
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(message="different"),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CURSOR_DISPATCH_CONFLICT"


@pytest.mark.asyncio
async def test_ledger_conflict_raises() -> None:
    ledger = CursorDispatchLedger.instance()
    admission = CursorDispatchResponse(
        admitted=True,
        dispatch_id="d1",
        thread_id="t1",
        model_id="composer-2.5",
    )
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id="d1",
        message="a",
    )
    fp = ledger.fingerprint(req)
    ledger.admit(
        req=req,
        fingerprint=fp,
        execution_id=None,
        resolved_model="composer-2.5",
        admission=admission,
    )
    with pytest.raises(DispatchConflict):
        ledger.admit(
            req=req,
            fingerprint="other-fingerprint",
            execution_id=None,
            resolved_model="composer-2.5",
            admission=admission,
        )


@pytest.mark.asyncio
async def test_bus_client_599_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("down")

    inner = MagicMock()
    inner.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {"turns": [{"turn_number": 1}]},
        )
    )
    inner.post = _boom

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *_a, **_k: MagicMock(
            __aenter__=AsyncMock(return_value=inner),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    bus = CursorBusClient(token="tok")
    result = await bus.reply(
        thread_id="1",
        to_agent="dispatch",
        from_agent="cursor-sdk",
        subject="s",
        body="b",
    )
    assert result.status_code == 599


@pytest.mark.asyncio
async def test_bus_client_marks_inbox_and_sets_after_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = MagicMock()
    inner.get = AsyncMock(
        side_effect=[
            MagicMock(status_code=200, json=lambda: {"turns": []}),
            MagicMock(
                status_code=200,
                json=lambda: {"turns": [{"turn_number": 3}]},
            ),
        ]
    )
    inner.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"turn_number": 4})
    )

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *_a, **_k: MagicMock(
            __aenter__=AsyncMock(return_value=inner),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    bus = CursorBusClient(token="tok")
    result = await bus.reply(
        thread_id="1567",
        to_agent="dispatch",
        from_agent="cursor-sdk",
        subject="smoke",
        body="PONG",
    )
    assert result.status_code == 201
    assert inner.get.await_count == 2
    post_payload = inner.post.await_args.kwargs["json"]
    assert post_payload["after_turn"] == 3


@pytest.mark.asyncio
async def test_dispatch_implement_stub_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1589",
        model="cursor/composer-2.5",
        dispatch_id="disp-stub",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    bus = _mock_bus()
    emitted: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", _capture)

    def _stub_outcome(**_kwargs: object) -> SdkRunOutcome:
        return SdkRunOutcome(
            body="Implementing",
            status="finished",
            duration_ms=1200,
            tool_call_count=0,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _stub_outcome)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    bus.reply.assert_awaited_once()
    body = bus.reply.await_args.kwargs["body"]
    assert body.startswith("status: degraded\nreason: zero_tool_calls")
    assert "Implementing" in body
    assert emitted[0]["outcome"] == "degraded"
    assert emitted[0]["tool_call_count"] == 0


@pytest.mark.asyncio
async def test_dispatch_implement_success_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1590",
        model="cursor/composer-2.5",
        dispatch_id="disp-ok",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    bus = _mock_bus()
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return SdkRunOutcome(
            body="## Closeout\nfiles touched",
            status="finished",
            duration_ms=5000,
            tool_call_count=2,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    body = bus.reply.await_args.kwargs["body"]
    assert "status: degraded" not in body
    assert emitted[0]["outcome"] == "ok"
    assert emitted[0]["tool_call_count"] == 2
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1590", terminal_status="completed"
    )


@pytest.mark.asyncio
async def test_dispatch_consult_zero_tool_calls_not_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1600",
        model="cursor/composer-2.5",
        dispatch_id="disp-consult",
        message="---\ncontract: consult\n---\npacket",
        handoff_contract="consult",
    )
    bus = _mock_bus()
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )

    def _consult_outcome(**_kwargs: object) -> SdkRunOutcome:
        return SdkRunOutcome(
            body="Findings only",
            status="finished",
            duration_ms=800,
            tool_call_count=0,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _consult_outcome)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    body = bus.reply.await_args.kwargs["body"]
    assert body == "Findings only"
    assert emitted[0]["outcome"] == "ok"


@pytest.mark.asyncio
async def test_dispatch_exception_posts_failure_turn_and_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1601",
        model="cursor/composer-2.5",
        dispatch_id="disp-fail",
        message="hello",
    )
    bus = _mock_bus()
    failed: list[dict[str, object]] = []

    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_failed",
        lambda **kwargs: failed.append(dict(kwargs)),
    )

    def _boom(**_kwargs: object) -> SdkRunOutcome:
        raise RuntimeError("bridge died")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _boom)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1601", terminal_status="failed"
    )
    call = bus.reply.await_args
    assert call is not None
    assert "FAILED" in call.kwargs["subject"]
    assert "CURSOR_SDK_DISPATCH" in call.kwargs["body"]
    assert failed[0]["error"] == "bridge died"


@patch(
    "services.git_integration_worker.routes.cursor_sdk.asyncio.create_task",
    return_value=MagicMock(done=lambda: False),
)
def test_dispatch_admits_handoff_contract_fields(
    _mock_task: MagicMock, client: TestClient
) -> None:
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            handoff_contract="implement",
            prompt_preamble="Contract: bound implementation.",
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["admitted"] is True


@pytest.mark.asyncio
async def test_dispatch_home_config_error_posts_bus_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="99",
        model="cursor/composer-2.5",
        dispatch_id="disp-home-fail",
        message="hello",
    )
    bus = _mock_bus()

    def _boom(**_kwargs: Any) -> str:
        raise CursorHomeConfigError("no credential")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _boom)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="99", terminal_status="failed"
    )
    call = bus.reply.await_args
    assert call is not None
    assert "FAILED (home/auth)" in call.kwargs["subject"]
    assert "CURSOR_HOME_CONFIG" in call.kwargs["body"]


@pytest.mark.asyncio
async def test_dispatch_timeout_posts_failure_and_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1607",
        model="cursor/composer-2.5",
        dispatch_id="disp-timeout",
        message="hello",
    )
    bus = _mock_bus()
    timeout_events: list[dict[str, object]] = []

    monkeypatch.setattr(route_mod, "_SDK_TIMEOUT_S", 0.01)
    monkeypatch.setattr(route_mod, "_SDK_TIMEOUT_BUFFER_S", 0.01)
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_timeout",
        lambda **kwargs: timeout_events.append(dict(kwargs)),
    )

    def _slow(**_kwargs: object) -> SdkRunOutcome:
        import time

        time.sleep(1.0)
        return SdkRunOutcome(
            body="late",
            status="finished",
            duration_ms=1000,
            tool_call_count=0,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _slow)

    await route_mod._run_sdk_dispatch(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        bus=bus,
    )

    bus.reply.assert_awaited_once()
    assert "FAILED (timeout)" in bus.reply.await_args.kwargs["subject"]
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1607", terminal_status="failed"
    )
    assert timeout_events[0]["dispatch_id"] == "disp-timeout"
    assert timeout_events[0]["thread_id"] == "1607"


def test_active_work_busy_with_running_dispatch(client: TestClient) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="1",
        model="cursor/composer-2.5",
        dispatch_id="disp-1",
        message="hello",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="disp-1",
            thread_id="1",
            model_id="composer-2.5",
        ),
    )
    ledger.mark_running(dispatch_id="disp-1")

    resp = client.get("/api/v1/git/active-work")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cursor_dispatches"]["running"] == 1
    assert data["busy"] is True
