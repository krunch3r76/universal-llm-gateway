"""Unit tests for cursor-sdk dispatch route, registry, and bus client."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.app import create_app
from services.git_integration_worker.cursor_bus import CursorBusClient
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    DispatchConflict,
)
from services.git_integration_worker.cursor_home import (
    CursorHomeConfigError,
    CursorVenvConfigError,
)
from services.git_integration_worker.cursor_sdk_closeout import SdkRunOutcome
from services.git_integration_worker.cursor_sdk_context import CursorSdkParityError
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _sdk_outcome(**kwargs: object) -> SdkRunOutcome:
    defaults: dict[str, object] = {
        "body": "done",
        "status": "finished",
        "duration_ms": 100,
        "tool_call_count": 1,
        "sdk_request_id": "sdk-req-test",
        "request_id_source": "stream",
    }
    defaults.update(kwargs)
    return SdkRunOutcome(**defaults)


@pytest.fixture(autouse=True)
def _reset_git_probe_cache() -> None:
    from services.git_integration_worker.cursor_sdk_feature_probe import (
        clear_probe_cache,
    )

    clear_probe_cache()
    yield
    clear_probe_cache()


@pytest.fixture(autouse=True)
def _stub_dispatch_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admit-path tests must not require live fastmcp-remote / MCP token / Cursor auth.

    Individual tests may override ``validate_dispatch_context`` (e.g. parity-failure 422).
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda _repo: {
            "setting_sources": ["projectSettings", "user"],
            "mcp_server": "vortex",
            "mcp_bridge": "scripts/mcp-fastmcp-remote-bridge.py",
            "mcp_remote_cmd": "fastmcp-remote",
            "mcp_token_source": "test",
            "cursor_auth_source": "test",
            "user_rules_dir_present": False,
        },
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


@pytest.fixture(autouse=True)
def _noop_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch gate acquire/release to no-ops so route tests don't touch the real gate.

    Without this fixture the module-level _GATE would accumulate leaked active
    slots. The worker-thread release path must be stubbed too: tests that drive
    ``_run_sdk_sync`` directly have no spinning gate loop, so its real
    ``run_coroutine_threadsafe(...).result(timeout=30)`` would block for 30s and
    then raise.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    async def _noop_acquire(
        *,
        dispatch_id: str | None = None,
        timeout: float | None = None,
        caller_agent: str | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> str:
        del timeout, caller_agent, on_wait
        return dispatch_id or "test-slot"

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child_sync", lambda *_a, **_k: "released"
    )


@pytest.fixture(autouse=True)
def _stub_closeout_trigger(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub emit_implement_closeout_trigger so success-path tests skip real HTTP."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    stub = AsyncMock()
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", stub)
    return stub


def _mock_bus() -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    return bus


def _make_controller() -> WorkAdmissionController:
    """Real admission controller bound to the reset ledger singleton.

    Direct ``_run_sdk_dispatch_gated`` callers need a controller so the inner
    worker task can be spawned via ``create_tracked_task``; these tests stub
    ``_run_sdk_sync`` and never start a drain, so the controller stays idle.
    """
    return WorkAdmissionController(
        ledger=CursorDispatchLedger.instance(),
        worker_id="test-worker",
        pid=0,
        worker_started_at="test",
    )


def _dispatch_body(**overrides: Any) -> dict[str, Any]:
    base = {
        "thread_id": "1558",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
    }
    base.update(overrides)
    return base


def test_dispatch_missing_execution_id_422(client: TestClient) -> None:
    body = _dispatch_body()
    del body["execution_id"]
    resp = client.post("/api/v1/cursor/dispatch", json=body)
    assert resp.status_code == 422


def test_dispatch_untrusted_model_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _reject(_model: object) -> object:
        raise ValueError("untrusted model")

    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_sdk.resolve_cursor",
        _reject,
    )
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
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
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
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
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
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
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
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
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
        execution_id="exec-d1",
        message="a",
    )
    fp = ledger.fingerprint(req)
    ledger.admit(
        req=req,
        fingerprint=fp,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=admission,
    )
    with pytest.raises(DispatchConflict):
        ledger.admit(
            req=req,
            fingerprint="other-fingerprint",
            execution_id=req.execution_id,
            caller_agent=None,
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
    assert post_payload["allow_long_body"] is False


@pytest.mark.asyncio
async def test_bus_client_passes_allow_long_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner = MagicMock()
    inner.get = AsyncMock(
        side_effect=[
            MagicMock(status_code=200, json=lambda: {"turns": []}),
            MagicMock(status_code=200, json=lambda: {"turns": []}),
        ]
    )
    inner.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"turn_number": 2})
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_bus.make_async_client",
        lambda *_a, **_k: MagicMock(
            __aenter__=AsyncMock(return_value=inner),
            __aexit__=AsyncMock(return_value=False),
        ),
    )
    bus = CursorBusClient(token="tok")
    await bus.reply(
        thread_id="1",
        to_agent="dispatch",
        from_agent="cursor-sdk",
        subject="s",
        body="b",
        allow_long_body=True,
    )
    assert inner.post.await_args.kwargs["json"]["allow_long_body"] is True


@pytest.mark.asyncio
async def test_dispatch_large_result_posts_bounded_closeout_with_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    big_body = "x" * 8500
    req = CursorDispatchRequest(
        thread_id="1831",
        model="cursor/composer-2.5",
        dispatch_id="disp-big",
        execution_id="exec-big",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    bus = _mock_bus()

    def _big_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(
            body=big_body,
            duration_ms=130762,
            tool_call_count=85,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _big_outcome)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    reply_kwargs = bus.reply.await_args.kwargs
    assert len(reply_kwargs["body"]) <= 8000
    assert reply_kwargs["allow_long_body"] is True
    assert (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/disp-big.md"
        in reply_kwargs["body"]
    )
    sidecar = source_repo / "tmp/reviews/closeouts/disp-big.md"
    assert sidecar.read_text(encoding="utf-8") == big_body
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1831", terminal_status="completed"
    )


@pytest.mark.asyncio
async def test_dispatch_reply_413_emits_delivery_failed_and_terminates_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1831",
        model="cursor/composer-2.5",
        dispatch_id="disp-413",
        execution_id="exec-413",
        message="hello",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(
        side_effect=[
            MagicMock(status_code=413, body={"reason": "body_too_large"}),
            MagicMock(status_code=201, body={}),
        ]
    )
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    delivery_failed: list[dict[str, object]] = []
    completed: list[dict[str, object]] = []
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_delivery_failed",
        lambda **kwargs: delivery_failed.append(dict(kwargs)),
    )
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: completed.append(dict(kwargs)),
    )

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome()

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert bus.reply.await_count == 2
    assert delivery_failed[0]["status_code"] == 413
    assert delivery_failed[0]["execution_id"] == "exec-413"
    assert delivery_failed[0]["sidecar_ref"] == (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/disp-413.md"
    )
    assert completed[0]["outcome"] == "delivery_failed"
    sidecar = source_repo / "tmp/reviews/closeouts/disp-413.md"
    assert sidecar.is_file()
    fallback_body = bus.reply.await_args_list[1].kwargs["body"]
    assert delivery_failed[0]["sidecar_ref"] in fallback_body
    assert "DELIVERY FAILED" in bus.reply.await_args_list[1].kwargs["subject"]
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1831", terminal_status="failed"
    )


@pytest.mark.asyncio
async def test_dispatch_implement_stub_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1589",
        model="cursor/composer-2.5",
        dispatch_id="disp-stub",
        execution_id="exec-stub",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    bus = _mock_bus()
    emitted: list[dict[str, object]] = []

    def _capture(**kwargs: object) -> None:
        emitted.append(dict(kwargs))

    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", _capture)

    def _stub_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(
            body="Implementing",
            duration_ms=1200,
            tool_call_count=0,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _stub_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.reply.assert_awaited_once()
    body = bus.reply.await_args.kwargs["body"]
    payload = json.loads(body)
    assert payload["status"] == "partial"
    assert "zero_tool_calls" in payload["summary"]
    sidecar_ref = (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/disp-stub.md"
    )
    assert payload["evidence_uris"]["artifact_paths"] == [sidecar_ref]
    assert len(body) <= 8000
    sidecar = source_repo / "tmp/reviews/closeouts/disp-stub.md"
    assert sidecar.is_file()
    assert "Implementing" in sidecar.read_text(encoding="utf-8")
    assert emitted[0]["outcome"] == "degraded"
    assert emitted[0]["tool_call_count"] == 0
    assert emitted[0]["execution_id"] == "exec-stub"


@pytest.mark.asyncio
async def test_dispatch_implement_success_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1590",
        model="cursor/composer-2.5",
        dispatch_id="disp-ok",
        execution_id="exec-ok",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    _seed_running_row(req, contract="implement")
    ledger = CursorDispatchLedger.instance()
    ledger.set_wt_baseline(dispatch_id=req.dispatch_id, wt_baseline="{}")
    bus = _mock_bus()
    emitted: list[dict[str, object]] = []

    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: emitted.append(dict(kwargs)),
    )

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(
            body="## Closeout\nfiles touched",
            duration_ms=5000,
            tool_call_count=2,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    body = bus.reply.await_args.kwargs["body"]
    assert "status: degraded" not in body
    assert bus.reply.await_args.kwargs["allow_long_body"] is True
    assert emitted[0]["outcome"] == "ok"
    assert emitted[0]["tool_call_count"] == 2
    assert emitted[0]["execution_id"] == "exec-ok"
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1590", terminal_status="completed"
    )


@pytest.mark.asyncio
async def test_dispatch_implement_pin_satisfied_cortex_uri_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Friction 20588: satisfied cortex pin puts cortex URI first in artifact_paths."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    cortex_root = tmp_path / "cortex"
    rel = "notes/system/closeout-pin.md"
    cortex_path = cortex_root / rel
    cortex_path.parent.mkdir(parents=True)
    cortex_path.write_text("# pin closeout", encoding="utf-8")
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))

    packet_file = source_repo / "packet.md"
    packet_file.write_text(
        f"---\ncontract: implement\n---\n"
        f"<scope>\nFiles expected: - `cortex://{rel}`\n</scope>\n",
        encoding="utf-8",
    )

    async def _mock_post(**_kwargs: object) -> dict[str, object]:
        return {"uri": f"cortex://{rel}", "created": False}

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_deliverables.default_post_pinned_deliverable",
        _mock_post,
    )

    req = CursorDispatchRequest(
        thread_id="20588",
        model="cursor/composer-2.5",
        dispatch_id="disp-pin",
        execution_id="exec-pin",
        packet_path="packet.md",
        handoff_contract="implement",
    )
    bus = _mock_bus()

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(
            body="## Closeout\npin satisfied",
            duration_ms=500,
            tool_call_count=2,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    payload = json.loads(bus.reply.await_args.kwargs["body"])
    cortex_uri = f"cortex://{rel}"
    sidecar_ref = (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/disp-pin.md"
    )
    assert payload["evidence_uris"]["artifact_paths"][0] == cortex_uri
    assert payload["evidence_uris"]["artifact_paths"][-1] == sidecar_ref
    sidecar = source_repo / "tmp/reviews/closeouts/disp-pin.md"
    assert sidecar.is_file()


@pytest.mark.asyncio
async def test_dispatch_reply_targets_caller_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: bus replies target caller_agent when present."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1608",
        model="cursor/composer-2.5",
        dispatch_id="disp-caller",
        execution_id="exec-caller",
        caller_agent="claude-web",
        message="hello",
    )
    bus = _mock_bus()

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome()

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert bus.reply.await_args.kwargs["to_agent"] == "claude-web"


@pytest.mark.asyncio
async def test_dispatch_reply_defaults_to_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC6: bus replies default to dispatch when caller_agent absent."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1609",
        model="cursor/composer-2.5",
        dispatch_id="disp-default",
        execution_id="exec-default",
        message="hello",
    )
    bus = _mock_bus()

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome()

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert bus.reply.await_args.kwargs["to_agent"] == "dispatch"


@pytest.mark.asyncio
async def test_dispatch_consult_zero_tool_calls_not_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1600",
        model="cursor/composer-2.5",
        dispatch_id="disp-consult",
        execution_id="exec-consult",
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
        return _sdk_outcome(
            body="Findings only",
            duration_ms=800,
            tool_call_count=0,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _consult_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    body = bus.reply.await_args.kwargs["body"]
    payload = json.loads(body)
    sidecar_ref = (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/disp-consult.md"
    )
    assert payload["evidence_uris"]["artifact_paths"] == [sidecar_ref]
    sidecar = source_repo / "tmp/reviews/closeouts/disp-consult.md"
    assert sidecar.read_text(encoding="utf-8") == "Findings only"
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
        execution_id="exec-fail",
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

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1601", terminal_status="failed"
    )
    call = bus.reply.await_args
    assert call is not None
    assert "FAILED" in call.kwargs["subject"]
    assert "CURSOR_SDK_DISPATCH" in call.kwargs["body"]
    assert failed[0]["error"] == "RuntimeError: bridge died"
    assert failed[0]["execution_id"] == "exec-fail"
    assert failed[0]["degraded_reasons"] == ["worker_dispatch_failed"]


@pytest.mark.asyncio
async def test_finalize_failed_sdk_exceptions_emit_class_derived_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Roadmap item 4: two class-distinct SDK failures → distinguishable tokens."""
    from cursor_sdk.errors import BadRequestError, PermissionDeniedError

    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req_a = CursorDispatchRequest(
        thread_id="t-bad-req",
        model="cursor/composer-2.5",
        dispatch_id="disp-bad-req",
        execution_id="exec-bad-req",
        message="hello",
    )
    req_b = CursorDispatchRequest(
        thread_id="t-perm",
        model="cursor/composer-2.5",
        dispatch_id="disp-perm",
        execution_id="exec-perm",
        message="hello",
    )
    _seed_running_row(req_a)
    _seed_running_row(req_b)
    bus = _mock_bus()
    failed: list[dict[str, object]] = []
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_failed",
        lambda **kwargs: failed.append(dict(kwargs)),
    )

    await route_mod._finalize_failed(
        req=req_a,
        bus=bus,
        reply_to="dispatch",
        controller=_make_controller(),
        code="CURSOR_SDK_DISPATCH",
        message="bad",
        subject_suffix="FAILED",
        exc=BadRequestError("invalid"),
    )
    await route_mod._finalize_failed(
        req=req_b,
        bus=bus,
        reply_to="dispatch",
        controller=_make_controller(),
        code="CURSOR_SDK_DISPATCH",
        message="denied",
        subject_suffix="FAILED",
        exc=PermissionDeniedError("forbidden"),
    )

    reasons_a = failed[0]["degraded_reasons"]
    reasons_b = failed[1]["degraded_reasons"]
    assert reasons_a == ["sdk_bad_request"]
    assert reasons_b == ["sdk_permission_denied"]
    assert reasons_a != reasons_b


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
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
        execution_id="exec-home-fail",
        message="hello",
    )
    bus = _mock_bus()

    def _boom(**_kwargs: Any) -> str:
        raise CursorHomeConfigError("no credential")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _boom)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="99", terminal_status="failed"
    )
    call = bus.reply.await_args
    assert call is not None
    assert "FAILED (home/auth)" in call.kwargs["subject"]
    assert "CURSOR_HOME_CONFIG" in call.kwargs["body"]


def _fake_repo_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "repo-venv"
    bindir = venv / "bin"
    bindir.mkdir(parents=True)
    for exe in ("python", "pytest", "ruff"):
        (bindir / exe).touch()
    return venv


def test_run_sdk_sync_injects_venv_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bridge subprocess env must contain dispatch HOME/venv without mutating os.environ.

    The thread-local overlay (_dispatch_home_overlay) sets _dispatch_env.overrides.
    _bridge_subprocess_env_with_overlay reads those overrides when building the env
    dict the real bridge passes to Popen.  We verify by calling _bridge_subprocess_env
    (which is the patched overlay after _install_bridge_env_patch) inside the fake
    launch_bridge to simulate what the real bridge would produce.
    """
    import os

    from cursor_sdk import _bridge as _sdk_bridge

    from services.git_integration_worker.routes import cursor_sdk as route_mod

    repo_venv = _fake_repo_venv(tmp_path)
    dispatch_home = tmp_path / "dispatch-home"
    dispatch_home.mkdir()
    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(repo_venv))

    prev_home = os.environ.get("HOME")
    prev_venv = os.environ.get("VIRTUAL_ENV")
    prev_path = os.environ.get("PATH")

    captured: dict[str, str] = {}

    def _fake_launch_bridge(*_args: object, **_kwargs: object) -> MagicMock:
        # Simulate what the real bridge does: call the patched _bridge_subprocess_env
        # (which is _bridge_subprocess_env_with_overlay after _install_bridge_env_patch)
        # to get the env the bridge process would receive.
        captured.update(dict(_sdk_bridge._bridge_subprocess_env()))

        class _Run:
            def wait(self) -> MagicMock:
                return MagicMock(result="ok", status="finished", duration_ms=100)

            def conversation(self) -> list[object]:
                return []

        class _Agent:
            id = "agent-1"

            def send(self, _prompt: str) -> _Run:
                return _Run()

        client = MagicMock()
        client.create_agent.return_value = _Agent()
        client.close = MagicMock()
        return client

    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _did, **_: dispatch_home
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _repo: {})
    monkeypatch.setattr(
        route_mod, "resolve_cursor", lambda _mid: MagicMock(model_id="composer-2.5")
    )
    monkeypatch.setattr(
        route_mod,
        "build_model_selection",
        lambda _cfg, _ov: MagicMock(params=[]),
    )
    monkeypatch.setattr(
        route_mod,
        "build_agent_options",
        lambda _repo, _ws, _sel: MagicMock(local=True),
    )
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _fake_launch_bridge)
    monkeypatch.setattr(
        route_mod, "_start_heartbeat", lambda **_kw: (MagicMock(), MagicMock())
    )

    route_mod._run_sdk_sync(
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        prompt="hello",
        config_model_id="cursor/composer-2.5",
        selection_overrides=None,
        dispatch_id="disp-venv",
        thread_id="1752",
        resolved_model="composer-2.5",
        gate_loop=MagicMock(),
    )

    # Verify the bridge subprocess env (via overlay) has dispatch HOME/venv.
    assert captured["HOME"] == str(dispatch_home)
    assert captured["VIRTUAL_ENV"] == str(repo_venv)
    assert captured["PATH"].split(os.pathsep)[0] == str(repo_venv / "bin")
    # Verify os.environ was NOT mutated (thread-local isolation invariant).
    assert os.environ.get("HOME") == prev_home
    assert os.environ.get("VIRTUAL_ENV") == prev_venv
    assert os.environ.get("PATH") == prev_path


def test_dispatch_path_prepend_pins_cursor_agent_before_grok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bridge PATH must prefer verified ~/.local/bin/agent over ~/.grok/bin/agent."""
    import os

    from cursor_sdk import _bridge as _sdk_bridge

    from services.git_integration_worker.routes import cursor_sdk as route_mod

    operator_home = tmp_path / "operator-home"
    versions = operator_home / ".local/share/cursor-agent/versions/0.0-test"
    versions.mkdir(parents=True)
    cursor_binary = versions / "cursor-agent"
    cursor_binary.write_text("#!/bin/sh\necho cursor-agent\n", encoding="utf-8")
    cursor_binary.chmod(0o755)
    local_bin = operator_home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    (local_bin / "agent").symlink_to(cursor_binary)
    grok_bin = operator_home / ".grok" / "bin"
    grok_bin.mkdir(parents=True)
    grok_target = operator_home / ".grok" / "downloads" / "grok-linux-x86_64"
    grok_target.parent.mkdir(parents=True)
    grok_target.write_text("#!/bin/sh\necho grok\n", encoding="utf-8")
    grok_target.chmod(0o755)
    (grok_bin / "agent").symlink_to(grok_target)

    repo_venv = _fake_repo_venv(tmp_path)
    dispatch_home = tmp_path / "dispatch-home"
    dispatch_home.mkdir()
    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(repo_venv))
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.setenv(
        "PATH",
        f"{grok_bin}{os.pathsep}/usr/bin",
    )

    captured: dict[str, str] = {}

    def _fake_launch_bridge(*_args: object, **_kwargs: object) -> MagicMock:
        captured.update(dict(_sdk_bridge._bridge_subprocess_env()))

        class _Run:
            def wait(self) -> MagicMock:
                return MagicMock(result="ok", status="finished", duration_ms=100)

            def conversation(self) -> list[object]:
                return []

        class _Agent:
            id = "agent-1"

            def send(self, _prompt: str) -> _Run:
                return _Run()

        client = MagicMock()
        client.create_agent.return_value = _Agent()
        client.close = MagicMock()
        return client

    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _did, **_: dispatch_home
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _repo: {})
    monkeypatch.setattr(
        route_mod, "resolve_cursor", lambda _mid: MagicMock(model_id="composer-2.5")
    )
    monkeypatch.setattr(
        route_mod,
        "build_model_selection",
        lambda _cfg, _ov: MagicMock(params=[]),
    )
    monkeypatch.setattr(
        route_mod,
        "build_agent_options",
        lambda _repo, _ws, _sel: MagicMock(local=True),
    )
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _fake_launch_bridge)
    monkeypatch.setattr(route_mod, "operator_real_home", lambda: operator_home)
    monkeypatch.setattr(
        route_mod, "_start_heartbeat", lambda **_kw: (MagicMock(), MagicMock())
    )

    route_mod._run_sdk_sync(
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        prompt="hello",
        config_model_id="cursor/composer-2.5",
        selection_overrides=None,
        dispatch_id="disp-path-pin",
        thread_id="1752",
        resolved_model="composer-2.5",
        gate_loop=MagicMock(),
    )

    path_parts = captured["PATH"].split(os.pathsep)
    local_bin = str(operator_home / ".local" / "bin")
    assert path_parts[0] == str(repo_venv / "bin")
    assert path_parts[1] == local_bin
    assert path_parts.index(local_bin) < path_parts.index(str(grok_bin))


@pytest.mark.asyncio
async def test_dispatch_venv_config_error_posts_bus_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1752",
        model="cursor/composer-2.5",
        dispatch_id="disp-venv-fail",
        execution_id="exec-venv-fail",
        message="hello",
    )
    bus = _mock_bus()
    launch_called = False
    dispatch_home = tmp_path / "dispatch-home"
    dispatch_home.mkdir()

    def _track_launch(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal launch_called
        launch_called = True
        return MagicMock()

    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(tmp_path / "missing-venv"))
    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _did, **_: dispatch_home
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _repo: {})
    monkeypatch.setattr(
        route_mod, "resolve_cursor", lambda _mid: MagicMock(model_id="composer-2.5")
    )
    monkeypatch.setattr(
        route_mod,
        "build_model_selection",
        lambda _cfg, _ov: MagicMock(params=[]),
    )
    monkeypatch.setattr(
        route_mod,
        "build_agent_options",
        lambda _repo, _ws, _sel: MagicMock(local=True),
    )
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _track_launch)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert not launch_called
    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1752", terminal_status="failed"
    )
    call = bus.reply.await_args
    assert call is not None
    assert "FAILED (venv config)" in call.kwargs["subject"]
    assert "CURSOR_VENV_CONFIG" in call.kwargs["body"]


@pytest.mark.asyncio
async def test_dispatch_timeout_posts_failure_and_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="1607",
        model="cursor/composer-2.5",
        dispatch_id="disp-timeout",
        execution_id="exec-timeout",
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
        return _sdk_outcome(body="late", duration_ms=1000, tool_call_count=0)

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _slow)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.reply.assert_awaited_once()
    assert "FAILED (timeout)" in bus.reply.await_args.kwargs["subject"]
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="1607", terminal_status="failed"
    )
    assert timeout_events[0]["dispatch_id"] == "disp-timeout"
    assert timeout_events[0]["thread_id"] == "1607"
    assert timeout_events[0]["execution_id"] == "exec-timeout"


@pytest.mark.asyncio
async def test_closeout_fires_trigger_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _stub_closeout_trigger: AsyncMock,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    packet = source_repo / "packet.md"
    packet.write_text(
        "---\ncontract: implement\nsource_ref: todo:x\n---\nbody", encoding="utf-8"
    )
    req = CursorDispatchRequest(
        thread_id="1865",
        model="cursor/composer-2.5",
        dispatch_id="disp-trig",
        execution_id="exec-trig",
        packet_path="packet.md",
        handoff_contract="implement",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(
        return_value=MagicMock(status_code=200, body={"turn_number": 9})
    )
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(tool_call_count=2)

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    _stub_closeout_trigger.assert_awaited_once()
    kwargs = _stub_closeout_trigger.await_args.kwargs
    assert kwargs["source_ref"] == "todo:x"
    assert kwargs["idempotency_key"].startswith("implement-closeout:")
    assert kwargs["idempotency_key"].endswith(":9")


@pytest.mark.asyncio
async def test_closeout_no_trigger_on_delivery_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _stub_closeout_trigger: AsyncMock,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1866",
        model="cursor/composer-2.5",
        dispatch_id="disp-notrig",
        execution_id="exec-notrig",
        message="hello",
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(
        side_effect=[
            MagicMock(status_code=413, body={"reason": "too_large"}),
            MagicMock(status_code=201, body={}),
        ]
    )
    bus.terminate_dispatch = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(route_mod, "emit_sdk_worker_delivery_failed", lambda **_k: None)
    monkeypatch.setattr(route_mod, "emit_sdk_worker_completed", lambda **_k: None)

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome()

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    _stub_closeout_trigger.assert_not_awaited()


def _seed_running_row(req: CursorDispatchRequest, *, contract: str = "consult") -> None:
    """Admit + mark_running a real ledger row so finalize can mark it terminal."""
    ledger = CursorDispatchLedger.instance()
    from services.git_integration_worker.config import load_config

    source_repo = str(load_config().source_repo.resolve())
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=req.caller_agent,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        contract=contract,
        worker_instance="lazy",
    )
    ledger.mark_running(dispatch_id=req.dispatch_id)


def _row_status(dispatch_id: str) -> tuple[str, str | None]:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return row["status"], row["terminal_status"]


@pytest.mark.asyncio
async def test_worker_base_exception_marks_terminal_and_delivers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-silent-path: a BaseException from the worker run must still finalize.

    A dying fastmcp bridge can surface as BaseException/BaseExceptionGroup, which
    a narrow ``except Exception`` would let escape — leaving the row stuck
    ``running`` with zero delivery (the P0 orphan signature). The finalize path
    must mark terminal ``failed`` and post an error envelope.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="2680",
        model="cursor/composer-2.5",
        dispatch_id="disp-base-exc",
        execution_id="exec-base-exc",
        caller_agent="claude-web",
        message="verify",
    )
    _seed_running_row(req)
    bus = _mock_bus()
    failed: list[dict[str, object]] = []
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_failed",
        lambda **kwargs: failed.append(dict(kwargs)),
    )

    class _BridgeDied(BaseException):
        pass

    def _boom(**_kwargs: object) -> SdkRunOutcome:
        raise _BridgeDied("bridge subprocess vanished")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _boom)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert _row_status(req.dispatch_id) == ("failed", "failed")
    bus.reply.assert_awaited_once()
    bus.terminate_dispatch.assert_awaited_once_with(
        thread_id="2680", terminal_status="failed"
    )
    assert failed and "bridge subprocess vanished" in str(failed[0]["error"])


@pytest.mark.asyncio
async def test_closeout_exception_marks_terminal_and_delivers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No-silent-path: an exception during closeout/delivery must still finalize.

    The worker run succeeds but the post-run finalize (sidecar/cortex/bus) raises;
    the row must reach terminal ``failed`` and an error envelope must be delivered
    rather than leaving a stuck ``running`` orphan.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="2681",
        model="cursor/composer-2.5",
        dispatch_id="disp-closeout-exc",
        execution_id="exec-closeout-exc",
        message="verify",
    )
    _seed_running_row(req)
    bus = _mock_bus()

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome()

    async def _boom_delivery(**_kwargs: object) -> Any:
        raise RuntimeError("sidecar write exploded")

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "prepare_closeout_delivery_async", _boom_delivery)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert _row_status(req.dispatch_id) == ("failed", "failed")
    bus.reply.assert_awaited()
    bus.terminate_dispatch.assert_awaited_with(
        thread_id="2681", terminal_status="failed"
    )


def test_active_work_busy_with_running_dispatch(client: TestClient) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="1",
        model="cursor/composer-2.5",
        dispatch_id="disp-1",
        execution_id="exec-active-1",
        message="hello",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="disp-1",
            thread_id="1",
            model_id="composer-2.5",
        ),
    )
    ledger.mark_running(dispatch_id="disp-1")
    ledger.register_task("disp-1", MagicMock(done=lambda: False))

    resp = client.get("/api/v1/git/active-work")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cursor_dispatches"]["running"] == 1
    assert data["busy"] is True


def _seed_active_writer(
    *,
    dispatch_id: str = "active-writer",
    contract: str = "implement",
    worker_instance: str = "lazy",
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="seed",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="---\ncontract: implement\n---\nseed",
        handoff_contract=contract,
    )
    from services.git_integration_worker.config import load_config

    source_repo = str(load_config().source_repo.resolve())
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="seed",
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        contract=contract,
        worker_instance=worker_instance,
    )


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_read_only_admits_while_writer_active(
    _mock_task: MagicMock, client: TestClient
) -> None:
    """AC2/AC3: read_only dispatch admits despite active writer."""
    _seed_active_writer()
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            dispatch_id="reader-disp",
            execution_id="exec-reader",
            read_only=True,
            handoff_contract="pure-mechanical",
            message="skill_suggest(read_only)",
        ),
    )
    assert resp.status_code == 200
    assert resp.json()["admitted"] is True


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_read_only_implement_conflict_422(
    _mock_task: MagicMock, client: TestClient
) -> None:
    """AC4: read_only + implement contract rejected at worker."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            read_only=True,
            handoff_contract="implement",
            message="---\ncontract: implement\n---\np",
        ),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_READONLY_IMPLEMENT_CONFLICT"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_second_implement_writer_202(_mock_task: MagicMock, client: TestClient) -> None:
    """AC1: second implement writer on same repo returns 202 queued."""
    _seed_active_writer(dispatch_id="impl-active")
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            dispatch_id="impl-second",
            execution_id="exec-impl-second",
            handoff_contract="implement",
            message="---\ncontract: implement\n---\np",
        ),
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_second_writer_202(_mock_task: MagicMock, client: TestClient) -> None:
    """AC1: second non-read-only writer while lease held returns 202 queued."""
    _seed_active_writer(dispatch_id="writer-active", contract="light-bounded")
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            dispatch_id="writer-second",
            execution_id="exec-writer-second",
            handoff_contract="light-bounded",
            message="---\ncontract: light-bounded\n---\nsecond writer",
        ),
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"


def _wt_baseline_column(dispatch_id: str) -> str | None:
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT wt_baseline FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    return None if row is None else row["wt_baseline"]


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_admit_implement_capture_failure_skips_wt_baseline(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC13: admit path leaves wt_baseline NULL when capture_wt_baseline returns None."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(route_mod, "capture_wt_baseline_with_hashes", lambda _repo: None)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            handoff_contract="implement",
            message="---\ncontract: implement\n---\np",
        ),
    )
    assert resp.status_code == 200
    assert _wt_baseline_column("disp-1") is None


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_admit_pure_mechanical_capture_scheduled_like_implement(
    _mock_task: MagicMock,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pure-mechanical admit uses the same async drive path as implement (not sync baseline)."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(route_mod, "capture_wt_baseline_with_hashes", lambda _repo: None)
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_dispatch_body(
            handoff_contract="pure-mechanical",
            message="---\ncontract: implement\n---\np",
        ),
    )
    assert resp.status_code == 200
    assert _wt_baseline_column("disp-1") is None


@pytest.mark.asyncio
async def test_gated_pure_mechanical_captures_wt_baseline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_run_sdk_dispatch_gated must persist wt_baseline for pure-mechanical handoff."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="gate-pm-1",
        model="cursor/composer-2.5",
        dispatch_id="gate-pm-disp",
        execution_id="exec-gate-pm",
        handoff_contract="pure-mechanical",
        message="---\ncontract: implement\n---\np",
    )
    fake_baseline = {
        "codes": {},
        "hashes": {},
        "outside_repo": [],
        "admit_head": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    }
    set_calls: list[str] = []

    def _track_set(*, dispatch_id: str, wt_baseline: str) -> None:
        set_calls.append(wt_baseline)

    minimal_outcome = _sdk_outcome(body="done", tool_call_count=0)

    monkeypatch.setattr(
        route_mod,
        "acquire_sdk_dispatch_slot",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        route_mod,
        "capture_wt_baseline_with_hashes",
        lambda _repo: fake_baseline,
    )
    monkeypatch.setattr(ledger, "set_wt_baseline", _track_set)
    monkeypatch.setattr(route_mod, "_run_sdk_sync", lambda **_kw: minimal_outcome)
    monkeypatch.setattr(route_mod, "_deliver_sdk_closeout", AsyncMock())
    monkeypatch.setattr(route_mod, "_terminate_link", AsyncMock())
    monkeypatch.setattr(
        route_mod,
        "_mark_terminal_and_promote",
        AsyncMock(),
    )

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=tmp_path,
        dispatch_workspace=tmp_path,
        bus=AsyncMock(),
        controller=_make_controller(),
        contract="pure-mechanical",
    )

    assert len(set_calls) == 1
    assert json.loads(set_calls[0])["admit_head"] == fake_baseline["admit_head"]


@pytest.mark.asyncio
async def test_promoted_implement_capture_failure_skips_wt_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC13: promoted path leaves wt_baseline NULL when capture_wt_baseline returns None."""
    from services.git_integration_worker.cursor_dispatch_ledger import PromotedDispatch
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="promo-1",
        model="cursor/composer-2.5",
        dispatch_id="promo-disp",
        execution_id="exec-promo",
        handoff_contract="implement",
        message="---\ncontract: implement\n---\np",
    )
    from services.git_integration_worker.config import load_config

    source_repo = str(load_config().source_repo.resolve())
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=source_repo,
        contract="implement",
        worker_instance="lazy",
    )
    promoted = PromotedDispatch(
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        source_repo=source_repo,
        contract="implement",
        read_only=False,
        record_json=json.dumps(
            {
                "model": "cursor/composer-2.5",
                "message": "---\ncontract: implement\n---\np",
                "handoff_contract": "implement",
            }
        ),
    )
    set_calls: list[str] = []

    def _track_set(*, dispatch_id: str, wt_baseline: str) -> None:
        set_calls.append(wt_baseline)

    monkeypatch.setattr(route_mod, "capture_wt_baseline_with_hashes", lambda _repo: None)
    monkeypatch.setattr(ledger, "set_wt_baseline", _track_set)

    await route_mod._start_promoted_dispatch(
        promoted=promoted,
        controller=_make_controller(),
        request=None,
    )

    assert set_calls == []
    assert _wt_baseline_column(req.dispatch_id) is None


@pytest.mark.asyncio
async def test_finalize_implement_null_baseline_reports_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3/AC13: NULL wt_baseline at closeout drives capture_status=unavailable."""
    from services.git_integration_worker.cursor_sdk_closeout import CloseoutDelivery
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="unavail-1",
        model="cursor/composer-2.5",
        dispatch_id="disp-unavail-route",
        execution_id="exec-unavail",
        handoff_contract="implement",
        message="---\ncontract: implement\n---\np",
    )
    _seed_running_row(req, contract="implement")
    bus = _mock_bus()
    captured: dict[str, object] = {}

    async def _capture_delivery(**kwargs: object) -> CloseoutDelivery:
        captured.update(kwargs)
        from implement_admission.spec import CloseoutStatus

        return CloseoutDelivery(
            body='{"status":"partial","capture_status":"unavailable"}',
            sidecar_ref="workspaces://x",
            sidecar_path=source_repo / "sidecar.md",
            full_result_bytes=1,
            closeout_status=CloseoutStatus.PARTIAL,
        )

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(tool_call_count=2)

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(route_mod, "prepare_closeout_delivery_async", _capture_delivery)
    ledger = CursorDispatchLedger.instance()
    monkeypatch.setattr(ledger, "read_wt_baseline", lambda **_kwargs: None)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert captured.get("baseline") is None
    assert captured.get("deliverables_expected") is True


@pytest.mark.asyncio
async def test_closeout_failure_is_retryable_and_non_lossy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closeout/delivery exception must not discard the completed result:
    the error envelope is retryable and points at the persisted sidecar."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="4097",
        model="cursor/composer-2.5",
        dispatch_id="disp-closeout-fail",
        execution_id="exec-closeout-fail",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    bus = _mock_bus()

    def _stub_outcome(**_kwargs: object) -> SdkRunOutcome:
        return _sdk_outcome(
            body="Implemented everything",
            duration_ms=1000,
            tool_call_count=3,
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _stub_outcome)

    async def _boom(**_kwargs: object) -> None:
        raise AssertionError("structured closeout body exceeded 8000 chars (len=63000)")

    monkeypatch.setattr(route_mod, "prepare_closeout_delivery_async", _boom)

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    bus.reply.assert_awaited_once()
    kwargs = bus.reply.await_args.kwargs
    assert "FAILED (closeout)" in kwargs["subject"]
    env = json.loads(kwargs["body"].strip("`json\n "))
    assert env["code"] == "CURSOR_SDK_CLOSEOUT"
    assert env["retryable"] is True
    assert env["data"]["sidecar_ref"] == (
        "workspaces://universal-llm-gateway/tmp/reviews/closeouts/"
        "disp-closeout-fail.md"
    )


def test_run_sdk_sync_folds_stream_paths_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5/AC6: stream-only repo paths and list_artifacts fold into effects_manifest."""
    from services.git_integration_worker.cursor_sdk_stream_capture import (
        StreamCapture,
        ToolCallObservation,
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    repo_venv = _fake_repo_venv(tmp_path)
    dispatch_home = tmp_path / "dispatch-home"
    dispatch_home.mkdir()
    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(repo_venv))

    stream_capture = StreamCapture(
        tool_calls=(
            ToolCallObservation(
                call_id="c-stream",
                tool_name="write",
                status="completed",
                arg_bytes=10,
                result_bytes=0,
                truncated_fields=(),
                target_path="services/stream_only.py",
            ),
        )
    )

    class _Run:
        def stream(self):
            return iter([])

        def wait(self) -> MagicMock:
            return MagicMock(result="ok", status="finished", duration_ms=100)

        def conversation(self) -> list[object]:
            return []

    class _Agent:
        id = "agent-stream"

        def send(self, _prompt: str) -> _Run:
            return _Run()

        def list_artifacts(self) -> list[str]:
            return ["artifacts/from_bridge.md"]

    def _fake_launch_bridge(*_args: object, **_kwargs: object) -> MagicMock:
        client = MagicMock()
        client.create_agent.return_value = _Agent()
        client.close = MagicMock()
        return client

    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _did, **_: dispatch_home
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _repo: {})
    monkeypatch.setattr(
        route_mod, "resolve_cursor", lambda _mid: MagicMock(model_id="composer-2.5")
    )
    monkeypatch.setattr(
        route_mod,
        "build_model_selection",
        lambda _cfg, _ov: MagicMock(params=[]),
    )
    monkeypatch.setattr(
        route_mod,
        "build_agent_options",
        lambda _repo, _ws, _sel: MagicMock(local=True),
    )
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _fake_launch_bridge)
    monkeypatch.setattr(
        route_mod, "_start_heartbeat", lambda **_kw: (MagicMock(), MagicMock())
    )
    monkeypatch.setattr(route_mod, "observe_run_stream", lambda *_a, **_k: stream_capture)

    outcome = route_mod._run_sdk_sync(
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        prompt="hello",
        config_model_id="cursor/composer-2.5",
        selection_overrides=None,
        dispatch_id="disp-stream-fold",
        thread_id="1752",
        resolved_model="composer-2.5",
        gate_loop=MagicMock(),
    )

    assert outcome.effects_manifest is not None
    repo_paths = {
        entry.target for entry in outcome.effects_manifest.surfaces["repo"].entries
    }
    assert "services/stream_only.py" in repo_paths
    assert "artifacts/from_bridge.md" in repo_paths
    assert "stream" in outcome.effects_manifest.capture_sources
    assert "artifacts" in outcome.effects_manifest.capture_sources


@pytest.mark.asyncio
async def test_dispatch_success_emits_sdk019_completed_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sdk019: completed event carries dual request_id + degraded_reasons[]."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="sdk019",
        model="cursor/composer-2.5",
        dispatch_id="req-base-abc12345",
        execution_id="exec-sdk019",
        message="hello",
    )
    bus = _mock_bus()
    captured: list[dict[str, object]] = []

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=1,
            sdk_request_id="sdk-req-99",
            request_id_source="stream",
            sdk_run_id="run-99",
            sdk_agent_id="agent-99",
            degraded_reasons=("sdk_fs_mismatch",),
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: captured.append(dict(kwargs)),
    )

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert captured
    event = captured[0]
    assert event["request_id"] == "req-base"
    assert event["sdk_request_id"] == "sdk-req-99"
    assert event["request_id_source"] == "stream"
    assert event["sdk_run_id"] == "run-99"
    assert event["sdk_agent_id"] == "agent-99"
    assert event["degraded_reasons"] == ["sdk_fs_mismatch"]


def test_run_sdk_sync_local_bridge_post_wait_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Path-parity: local-bridge stream omits request message; post-wait supplies id."""
    from services.git_integration_worker.cursor_sdk_stream_capture import StreamCapture
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    repo_venv = _fake_repo_venv(tmp_path)
    dispatch_home = tmp_path / "dispatch-home"
    dispatch_home.mkdir()
    monkeypatch.setenv("CURSOR_SDK_VENV_PATH", str(repo_venv))

    stream_capture = StreamCapture(tool_calls=())

    class _Run:
        request_id = "run-post-wait-id"

        def events(self):
            return iter([])

        def wait(self) -> MagicMock:
            return MagicMock(
                result="ok",
                status="finished",
                duration_ms=100,
                request_id="result-post-wait-id",
                git=None,
            )

        def conversation(self) -> list[object]:
            return [object()]

    class _Agent:
        id = "agent-post-wait"

        def send(self, _prompt: str) -> _Run:
            return _Run()

        def list_artifacts(self) -> list[str]:
            return []

    def _fake_launch_bridge(*_args: object, **_kwargs: object) -> MagicMock:
        client = MagicMock()
        client.create_agent.return_value = _Agent()
        client.close = MagicMock()
        return client

    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _did, **_: dispatch_home
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _repo: {})
    monkeypatch.setattr(
        route_mod, "resolve_cursor", lambda _mid: MagicMock(model_id="composer-2.5")
    )
    monkeypatch.setattr(
        route_mod,
        "build_model_selection",
        lambda _cfg, _ov: MagicMock(params=[]),
    )
    monkeypatch.setattr(
        route_mod,
        "build_agent_options",
        lambda _repo, _ws, _sel: MagicMock(local=True),
    )
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _fake_launch_bridge)
    monkeypatch.setattr(
        route_mod, "_start_heartbeat", lambda **_kw: (MagicMock(), MagicMock())
    )
    monkeypatch.setattr(route_mod, "observe_run_stream", lambda *_a, **_k: stream_capture)

    outcome = route_mod._run_sdk_sync(
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        prompt="hello",
        config_model_id="cursor/composer-2.5",
        selection_overrides=None,
        dispatch_id="disp-post-wait-req",
        thread_id="1752",
        resolved_model="composer-2.5",
        gate_loop=MagicMock(),
    )

    assert outcome.sdk_request_id == "result-post-wait-id"
    assert outcome.request_id_source == "post_wait"
    assert outcome.degraded_reasons == ("sdk_git_probe_absent",)


@pytest.mark.asyncio
async def test_dispatch_success_emits_post_wait_request_id_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="post-wait-emit",
        model="cursor/composer-2.5",
        dispatch_id="req-post-wait-abc12345",
        execution_id="exec-post-wait",
        message="hello",
    )
    bus = _mock_bus()
    captured: list[dict[str, object]] = []

    def _ok_outcome(**_kwargs: object) -> SdkRunOutcome:
        return SdkRunOutcome(
            body="done",
            status="finished",
            duration_ms=100,
            tool_call_count=1,
            sdk_request_id="sdk-req-post-wait",
            request_id_source="post_wait",
        )

    monkeypatch.setattr(route_mod, "_run_sdk_sync", _ok_outcome)
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_completed",
        lambda **kwargs: captured.append(dict(kwargs)),
    )

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    assert captured
    event = captured[0]
    assert event["sdk_request_id"] == "sdk-req-post-wait"
    assert event["request_id_source"] == "post_wait"
    assert event["sdk_request_id"] is not None


def test_finalize_request_id_wire_point_receives_run_and_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wire-point liveness: finalize_request_id_capture sees non-null run and result."""
    from services.git_integration_worker.cursor_sdk_stream_capture import StreamCapture
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    seen: dict[str, object | None] = {"run": None, "result": None}

    def _capture_finalize(capture, *, run=None, result=None):
        seen["run"] = run
        seen["result"] = result
        return capture

    monkeypatch.setattr(route_mod, "finalize_request_id_capture", _capture_finalize)
    monkeypatch.setattr(
        route_mod,
        "observe_run_stream",
        lambda *_a, **_k: StreamCapture(tool_calls=()),
    )

    class _Run:
        request_id = "run-wire"

        def events(self):
            return iter([])

        def wait(self):
            return type("Result", (), {"status": "finished", "duration_ms": 1, "result": "ok", "request_id": "res-wire", "git": None})()

        def conversation(self):
            return [object()]

    class _Agent:
        id = "agent-wire"

        def send(self, _prompt):
            return _Run()

    def _fake_launch_bridge(*_a, **_k):
        client = MagicMock()
        client.create_agent.return_value = _Agent()
        client.close = MagicMock()
        return client

    monkeypatch.setattr(
        route_mod, "setup_cursor_dispatch_home", lambda _d, **_: Path("/tmp/dhome")
    )
    monkeypatch.setattr(route_mod, "validate_dispatch_context", lambda _r: {})
    monkeypatch.setattr(route_mod, "resolve_cursor", lambda _m: MagicMock(model_id="m"))
    monkeypatch.setattr(route_mod, "build_model_selection", lambda _c, _o: MagicMock(params=[]))
    monkeypatch.setattr(route_mod, "build_agent_options", lambda *_a, **_k: MagicMock(local=True))
    monkeypatch.setattr(route_mod.Client, "launch_bridge", _fake_launch_bridge)
    monkeypatch.setattr(route_mod, "_start_heartbeat", lambda **_k: (MagicMock(), MagicMock()))
    monkeypatch.setattr(route_mod, "resolve_repo_venv", lambda **_k: Path("/tmp/venv"))
    monkeypatch.setattr(route_mod, "validate_repo_venv", lambda _v: None)

    route_mod._run_sdk_sync(
        source_repo=route_mod._CONFIG.source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        prompt="x",
        config_model_id="cursor/composer-2.5",
        selection_overrides=None,
        dispatch_id="wire-point",
        thread_id="t",
        resolved_model="composer-2.5",
        gate_loop=MagicMock(),
    )

    assert seen["run"] is not None
    assert seen["result"] is not None


_WORKER_TERMINAL_SIGNALS = frozenset(
    {
        "frontier.sdk.worker.completed",
        "frontier.sdk.worker.failed",
        "frontier.sdk.worker.timeout",
        "frontier.sdk.worker.orphaned",
        "frontier.sdk.worker.cancelled",
    }
)


@pytest.fixture(autouse=True)
def _reset_terminal_emitted_registry() -> None:
    from services.git_integration_worker.cursor_sdk_events import (
        reset_terminal_emitted_registry,
    )

    reset_terminal_emitted_registry()
    yield
    reset_terminal_emitted_registry()


def _install_event_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []

    def _record(signal: str, **payload: object) -> None:
        events.append((signal, dict(payload)))

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.record",
        _record,
    )
    return events


def _worker_terminals_for(
    events: list[tuple[str, dict[str, object]]], dispatch_id: str
) -> list[tuple[str, dict[str, object]]]:
    return [
        (signal, payload)
        for signal, payload in events
        if signal in _WORKER_TERMINAL_SIGNALS
        and payload.get("dispatch_id") == dispatch_id
    ]


def _assert_one_terminal_before_lease(
    events: list[tuple[str, dict[str, object]]], dispatch_id: str
) -> None:
    terminals = _worker_terminals_for(events, dispatch_id)
    assert len(terminals) == 1
    term_idx = events.index(terminals[0])
    lease_indices = [
        idx
        for idx, (signal, payload) in enumerate(events)
        if signal == "frontier.sdk.worker.lease.released"
        and payload.get("dispatch_id") == dispatch_id
    ]
    if lease_indices:
        assert term_idx < lease_indices[0]


@pytest.mark.parametrize(
    "scenario",
    [
        "home_config",
        "venv_config",
        "stale_reclaim",
        "nest_park_transfer_fail",
    ],
)
@pytest.mark.asyncio
async def test_failure_paths_emit_one_terminal_before_lease(
    scenario: str,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    events = _install_event_recorder(monkeypatch)
    dispatch_id = f"disp-terminal-{scenario}"

    if scenario == "home_config":
        req = CursorDispatchRequest(
            thread_id="t-home",
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="hello",
        )
        _seed_running_row(req)
        bus = _mock_bus()

        def _raise_home(**_kwargs: object) -> SdkRunOutcome:
            raise CursorHomeConfigError("no credential")

        monkeypatch.setattr(route_mod, "_run_sdk_sync", _raise_home)
        await route_mod._run_sdk_dispatch_gated(
            req=req,
            source_repo=route_mod._CONFIG.source_repo,
            dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
            bus=bus,
            controller=_make_controller(),
        )
    elif scenario == "venv_config":
        req = CursorDispatchRequest(
            thread_id="t-venv",
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="hello",
        )
        _seed_running_row(req)
        bus = _mock_bus()

        def _raise_venv(**_kwargs: object) -> SdkRunOutcome:
            raise CursorVenvConfigError("bad venv")

        monkeypatch.setattr(route_mod, "_run_sdk_sync", _raise_venv)
        await route_mod._run_sdk_dispatch_gated(
            req=req,
            source_repo=route_mod._CONFIG.source_repo,
            dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
            bus=bus,
            controller=_make_controller(),
        )
    elif scenario == "stale_reclaim":
        req = CursorDispatchRequest(
            thread_id="t-stale",
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="stale",
        )
        ledger = CursorDispatchLedger.instance()
        repo = str(route_mod._CONFIG.source_repo.resolve())
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=dispatch_id,
                thread_id=req.thread_id,
                model_id="composer-2.5",
            ),
            source_repo=repo,
        )
        ledger.mark_running(dispatch_id=dispatch_id)
        controller = _make_controller()
        await route_mod.reconcile_stale_leases(controller)
    elif scenario == "nest_park_transfer_fail":
        ledger = CursorDispatchLedger.instance()
        repo = str(route_mod._CONFIG.source_repo.resolve())
        parent = CursorDispatchRequest(
            thread_id="t-parent",
            model="cursor/composer-2.5",
            dispatch_id="parent-nest",
            execution_id="exec-parent-nest",
            message="---\ncontract: implement\n---\nparent",
            handoff_contract="implement",
        )
        ledger.admit(
            req=parent,
            fingerprint=ledger.fingerprint(parent),
            execution_id=parent.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=parent.dispatch_id,
                thread_id=parent.thread_id,
                model_id="composer-2.5",
            ),
            source_repo=repo,
            contract="implement",
        )
        ledger.mark_running(dispatch_id=parent.dispatch_id)
        monkeypatch.setattr(
            route_mod,
            "transfer_capacity_after_park",
            AsyncMock(side_effect=RuntimeError("transfer failed")),
        )
        resp = client.post(
            "/api/v1/cursor/dispatch",
            json=_dispatch_body(
                dispatch_id=dispatch_id,
                execution_id=f"exec-{dispatch_id}",
                nest_under="parent-nest",
                thread_id="t-child",
                handoff_contract="implement",
                message="---\ncontract: implement\n---\nchild",
            ),
        )
        assert resp.status_code == 503

    _assert_one_terminal_before_lease(events, dispatch_id)


@pytest.mark.asyncio
async def test_start_promoted_dispatch_draining503_demotes_to_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race fallback: promoted head survives drain as queued, not terminal failed."""
    from services.git_integration_worker.admission import Draining503
    from services.git_integration_worker.cursor_dispatch_ledger import PromotedDispatch
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    events = _install_event_recorder(monkeypatch)
    dispatch_id = "disp-draining-demote"
    req = CursorDispatchRequest(
        thread_id="t-promote",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="queued",
    )
    ledger = CursorDispatchLedger.instance()
    repo = str(route_mod._CONFIG.source_repo.resolve())
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=repo,
        contract="consult",
    )
    promoted = PromotedDispatch(
        dispatch_id=dispatch_id,
        thread_id=req.thread_id,
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        source_repo=repo,
        contract="consult",
        read_only=False,
        record_json='{"model":"cursor/composer-2.5","message":"queued"}',
        lease_key=repo,
    )
    controller = _make_controller()

    def _draining(*_args: object, **_kwargs: object) -> object:
        raise Draining503("draining")

    monkeypatch.setattr(
        "services.git_integration_worker.admission.WorkAdmissionController.try_admit",
        _draining,
    )
    await route_mod._start_promoted_dispatch(
        promoted=promoted,
        controller=controller,
        request=None,
    )

    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["terminal_status"] is None
    assert _worker_terminals_for(events, dispatch_id) == []


@pytest.mark.asyncio
async def test_mark_terminal_and_promote_skips_promote_while_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Holder closeout during drain leaves queued successors on the ledger."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    repo = str(route_mod._CONFIG.source_repo.resolve())
    holder = CursorDispatchRequest(
        thread_id="t-holder",
        model="cursor/composer-2.5",
        dispatch_id="holder-drain",
        execution_id="exec-holder-drain",
        message="holder",
    )
    successor = CursorDispatchRequest(
        thread_id="t-successor",
        model="cursor/composer-2.5",
        dispatch_id="successor-drain",
        execution_id="exec-successor-drain",
        message="successor",
    )
    ledger.admit(
        req=holder,
        fingerprint=ledger.fingerprint(holder),
        execution_id=holder.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=holder.dispatch_id,
            thread_id=holder.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=repo,
        contract="implement",
        worker_instance="test-worker",
    )
    ledger.mark_running(dispatch_id=holder.dispatch_id)
    queued = ledger.admit(
        req=successor,
        fingerprint=ledger.fingerprint(successor),
        execution_id=successor.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=successor.dispatch_id,
            thread_id=successor.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=repo,
        contract="implement",
        worker_instance="test-worker",
    )
    assert queued is not None
    assert queued.status == "queued"

    controller = _make_controller()
    controller.begin_drain(
        reason="test",
        intent_id="drain-test",
        drain_epoch=1,
    )
    promoted: list[str] = []

    async def _track_promote(*, lease_key: str, controller, request=None) -> None:
        promoted.append(lease_key)

    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", _track_promote)
    await route_mod._mark_terminal_and_promote(
        dispatch_id=holder.dispatch_id,
        terminal_status="completed",
        controller=controller,
        emit_tag="CURSOR_TEST_DRAIN",
    )

    assert promoted == []
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (successor.dispatch_id,),
        ).fetchone()
    assert row["status"] == "queued"


@pytest.mark.asyncio
async def test_mark_terminal_and_promote_promotes_after_drain_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-drain holder closeout still promotes the FIFO head."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    repo = str(route_mod._CONFIG.source_repo.resolve())
    holder = CursorDispatchRequest(
        thread_id="t-holder-live",
        model="cursor/composer-2.5",
        dispatch_id="holder-live",
        execution_id="exec-holder-live",
        message="holder",
    )
    successor = CursorDispatchRequest(
        thread_id="t-successor-live",
        model="cursor/composer-2.5",
        dispatch_id="successor-live",
        execution_id="exec-successor-live",
        message="successor",
    )
    ledger.admit(
        req=holder,
        fingerprint=ledger.fingerprint(holder),
        execution_id=holder.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=holder.dispatch_id,
            thread_id=holder.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=repo,
        contract="implement",
        worker_instance="test-worker",
    )
    ledger.mark_running(dispatch_id=holder.dispatch_id)
    queued = ledger.admit(
        req=successor,
        fingerprint=ledger.fingerprint(successor),
        execution_id=successor.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=successor.dispatch_id,
            thread_id=successor.thread_id,
            model_id="composer-2.5",
        ),
        source_repo=repo,
        contract="implement",
        worker_instance="test-worker",
    )
    assert queued is not None
    assert queued.status == "queued"

    controller = _make_controller()
    promoted: list[str] = []

    async def _track_promote(*, lease_key: str, controller, request=None) -> None:
        promoted.append(lease_key)

    monkeypatch.setattr(route_mod, "_promote_queued_for_lease", _track_promote)
    await route_mod._mark_terminal_and_promote(
        dispatch_id=holder.dispatch_id,
        terminal_status="completed",
        controller=controller,
        emit_tag="CURSOR_TEST_LIVE",
    )

    assert promoted == [repo]


@pytest.mark.asyncio
async def test_success_path_single_completed_no_unclassified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source_repo = tmp_path / "repo"
    source_repo.mkdir()
    req = CursorDispatchRequest(
        thread_id="1599",
        model="cursor/composer-2.5",
        dispatch_id="disp-success-terminal",
        execution_id="exec-success-terminal",
        message="---\ncontract: implement\n---\npacket",
        handoff_contract="implement",
    )
    _seed_running_row(req, contract="implement")
    ledger = CursorDispatchLedger.instance()
    ledger.set_wt_baseline(dispatch_id=req.dispatch_id, wt_baseline="{}")
    bus = _mock_bus()
    events = _install_event_recorder(monkeypatch)

    monkeypatch.setattr(
        route_mod,
        "_run_sdk_sync",
        lambda **_k: _sdk_outcome(
            body="## Closeout\nfiles touched",
            duration_ms=5000,
            tool_call_count=2,
        ),
    )

    await route_mod._run_sdk_dispatch_gated(
        req=req,
        source_repo=source_repo,
        dispatch_workspace=route_mod._CONFIG.dispatch_workspace,
        bus=bus,
        controller=_make_controller(),
    )

    terminals = _worker_terminals_for(events, req.dispatch_id)
    assert len(terminals) == 1
    assert terminals[0][0] == "frontier.sdk.worker.completed"
    assert all(
        payload.get("failure_layer") != "unclassified_terminal"
        for _sig, payload in events
        if payload.get("dispatch_id") == req.dispatch_id
    )


@pytest.mark.asyncio
async def test_finalize_failed_emits_when_error_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    req = CursorDispatchRequest(
        thread_id="t-no-error",
        model="cursor/composer-2.5",
        dispatch_id="disp-no-error",
        execution_id="exec-no-error",
        message="hello",
    )
    _seed_running_row(req)
    bus = _mock_bus()
    failed: list[dict[str, object]] = []
    monkeypatch.setattr(
        route_mod,
        "emit_sdk_worker_failed",
        lambda **kwargs: failed.append(dict(kwargs)),
    )

    await route_mod._finalize_failed(
        req=req,
        bus=bus,
        reply_to="dispatch",
        controller=_make_controller(),
        code="CURSOR_TEST_NO_ERROR",
        message="synthetic failure",
        subject_suffix="FAILED",
    )

    assert failed
    assert failed[0]["error"] == "CURSOR_TEST_NO_ERROR: synthetic failure"
    assert failed[0]["worker_error_code"] == "CURSOR_TEST_NO_ERROR"


def test_delete_route_cancel_queued(client: TestClient) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="del-t1",
        model="cursor/composer-2.5",
        dispatch_id="del-queued",
        execution_id="exec-del-queued",
        message="cancel me",
    )
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.dispatch_id,
                ledger.fingerprint(req),
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                1,
                "queued",
                '{"model":"cursor/composer-2.5","message":"cancel me"}',
                "/mnt/torus/projects/universal-llm-gateway",
                0,
            ),
        )
    resp = client.delete("/api/v1/cursor/dispatch/del-queued?reason=op-test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["terminal_status"] == "cancelled"


def test_delete_route_running_refuses_not_404(client: TestClient) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="del-run",
        model="cursor/composer-2.5",
        dispatch_id="del-running",
        execution_id="exec-del-running",
        message="running",
    )
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
            " message_present, status, record_json, source_repo, read_only) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                req.dispatch_id,
                ledger.fingerprint(req),
                req.thread_id,
                req.execution_id,
                "composer-2.5",
                1,
                "running",
                '{"model":"cursor/composer-2.5","message":"running"}',
                "/mnt/torus/projects/universal-llm-gateway",
                0,
            ),
        )
    resp = client.delete("/api/v1/cursor/dispatch/del-running")
    assert resp.status_code == 409
    assert resp.status_code != 404
    body = resp.json()
    assert body["code"] == "not_cancellable_running"
    assert body["retryable"] is False
    assert body["data"]["dispatch_id"] == "del-running"
