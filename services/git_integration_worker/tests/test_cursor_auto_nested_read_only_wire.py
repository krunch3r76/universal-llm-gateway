"""Regression: cursor-auto nested POST must wire read_only explicitly (11402 AC4)."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from services.git_integration_worker.app import create_app
from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_auto.handler import process_job
from services.git_integration_worker.cursor_auto.nested_sdk import (
    submit_nested_dispatch,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


@pytest.fixture
def worker_cfg(tmp_path: Path, git_repo: Path) -> WorkerConfig:
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    dispatch_ws = tmp_path / "dispatch_ws"
    dispatch_ws.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=8091,
        source_repo=git_repo,
        worktree_root=wt_root,
        dispatch_workspace=dispatch_ws,
        green_gate_cmd=["true"],
    )


@pytest.fixture
def client(
    worker_cfg: WorkerConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(worker_cfg.source_repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worker_cfg.worktree_root))
    monkeypatch.setenv(
        "GIT_INTEGRATION_DISPATCH_WORKSPACE", str(worker_cfg.dispatch_workspace)
    )
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    monkeypatch.setattr(route_mod, "_CONFIG", worker_cfg)
    monkeypatch.setattr(
        route_mod,
        "validate_dispatch_context",
        lambda *_a, **_k: {"setting_sources": ["projectSettings"]},
    )

    async def _noop_acquire(**kwargs: object) -> str:
        return str(kwargs.get("dispatch_id") or "slot")

    monkeypatch.setattr(route_mod, "acquire_sdk_dispatch_slot", _noop_acquire)
    monkeypatch.setattr(
        route_mod, "release_or_restore_for_child_sync", lambda *_a, **_k: "released"
    )
    monkeypatch.setattr(route_mod, "emit_implement_closeout_trigger", MagicMock())

    app = create_app()
    app.state.worker_config = worker_cfg
    return TestClient(app)


def _implement_job(**kwargs: object) -> AutoJob:
    fields: dict[str, object] = dict(
        job_id="j-implement",
        thread_id="11402",
        turn_number=1,
        subject="DIRECTIVE implement",
        body=(
            "TYPE: DIRECTIVE\n"
            "density: dense\n"
            "## Scope\n"
            "services/git_integration_worker/\n"
            "vision: wire read_only explicitly on nested POST\n"
        ),
        from_agent="cursor",
        to_agent="cursor-auto",
        desired_model="composer-2.5",
        desired_effort="medium",
        contract="implement",
    )
    fields.update(kwargs)
    return AutoJob(**fields)  # type: ignore[arg-type]


def _pass_through_admit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.gate_serialize.sdk_dispatch_gate_stats",
        lambda **_kw: {"active": 0, "queued": 0, "limit": 1},
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_turns",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.admit_gates.fetch_thread_status",
        AsyncMock(return_value="active"),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.CursorDispatchLedger.instance",
        lambda: MagicMock(lease_snapshot=MagicMock(return_value={})),
    )
    return bus


def test_handler_implement_passes_read_only_false_not_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: writable implement nests with explicit read_only=False."""
    bus = _pass_through_admit(monkeypatch)
    submit = AsyncMock(return_value={"ok": False, "error": "stop"})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_b_episode_discharge.maybe_discharge_failed_episode",
        lambda *_a, **_k: None,
    )
    asyncio.run(process_job(_implement_job(), bus=bus))
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["read_only"] is False


def test_handler_ask_passes_read_only_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: read-only contract still stamps read_only=True at the wire."""
    bus = _pass_through_admit(monkeypatch)
    submit = AsyncMock(return_value={"ok": False, "error": "stop"})
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.handler.submit_nested_dispatch",
        submit,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_b_episode_discharge.maybe_discharge_failed_episode",
        lambda *_a, **_k: None,
    )
    job = _implement_job(
        job_id="j-ask",
        contract="ask",
        body="How does nested read_only wiring work?",
        subject="ask: read_only wire",
    )
    asyncio.run(process_job(job, bus=bus))
    submit.assert_awaited_once()
    assert submit.await_args.kwargs["read_only"] is True


def test_nested_post_includes_read_only_false_on_lane_b(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1/AC3: submit_nested_dispatch JSON carries read_only=false for writable lane B."""
    captured: dict[str, object] = {}

    class _FakeCM:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> object:
            client = AsyncMock()

            async def _post(url: str, json: dict[str, object]) -> MagicMock:
                captured.update(json)
                resp = MagicMock()
                resp.status_code = 200
                resp.content = b'{"admitted": true}'
                resp.json.return_value = {"admitted": True}
                return resp

            client.post = _post
            return client

        async def __aexit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.nested_sdk.httpx.AsyncClient",
        _FakeCM,
    )
    asyncio.run(
        submit_nested_dispatch(
            _implement_job(),
            model_id="cursor/composer-2.5",
            handoff_contract="none",
            message="TYPE: DIRECTIVE\ncontract=implement",
            read_only=False,
            bind_job=False,
        )
    )
    assert captured["read_only"] is False
    assert captured["lane"] == "B"


def _cursor_auto_body(**overrides: Any) -> dict[str, Any]:
    base = {
        "thread_id": "11402",
        "model": "cursor/composer-2.5",
        "dispatch_id": "auto-wire-test",
        "execution_id": "exec-auto-wire-test",
        "handoff_contract": "none",
        "message": "TYPE: DIRECTIVE\ncontract=implement\n",
        "admitted_via": "cursor-auto",
        "close_contract": "auto",
        "lane": "B",
    }
    base.update(overrides)
    return base


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_giw_admits_cursor_auto_write_on_lane_b_with_explicit_read_only_false(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC3: explicit read_only=false + lane=B admits (no CURSOR_LANE_B_READ_ONLY)."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_cursor_auto_body(read_only=False),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "admitted"
    ledger = CursorDispatchLedger.instance()
    assert ledger.read_read_only(dispatch_id="auto-wire-test") is False


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_giw_rejects_omitted_read_only_none_contract_on_lane_b(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """Falsifier: omitted read_only on none + lane=B still 422 (default policy)."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json=_cursor_auto_body(),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "CURSOR_LANE_B_READ_ONLY"


@patch(
    "services.git_integration_worker.admission.WorkAdmissionController.create_tracked_task",
    return_value=MagicMock(done=lambda: False),
)
def test_giw_read_only_contract_stamps_ledger_read_only_one(
    _mock_task: MagicMock,
    client: TestClient,
) -> None:
    """AC3: read-only nested POST stamps read_only=1 in the ledger."""
    resp = client.post(
        "/api/v1/cursor/dispatch",
        json={
            "thread_id": "11402-ro",
            "model": "cursor/composer-2.5",
            "dispatch_id": "auto-read-only",
            "execution_id": "exec-auto-read-only",
            "handoff_contract": "sketch",
            "message": "ask: how does this work",
            "admitted_via": "cursor-auto",
            "read_only": True,
        },
    )
    assert resp.status_code == 200
    ledger = CursorDispatchLedger.instance()
    assert ledger.read_read_only(dispatch_id="auto-read-only") is True
