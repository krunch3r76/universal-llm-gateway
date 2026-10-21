"""Orphan reclaim stops the billing bridge before the row is failed (a:36215)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import JSONResponse

from services.git_integration_worker import cursor_sdk_orphan as orphan_mod
from services.git_integration_worker import cursor_sdk_park as park_mod
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_events import (
    reset_terminal_emitted_registry,
)
from services.git_integration_worker.cursor_sdk_orphan import register_active_client
from services.git_integration_worker.cursor_sdk_park import reclaim_orphan_holder
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _reset_terminal_emitted_registry() -> None:
    reset_terminal_emitted_registry()
    yield
    reset_terminal_emitted_registry()


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "parent-1",
        "execution_id": "exec-parent-1",
        "message": "parent",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admission(req: CursorDispatchRequest) -> CursorDispatchResponse:
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="composer-2.5",
    )


class _Bridge:
    """Env-matched cursor-sdk bridge stand-in for OS reap."""

    def __init__(self, pid: int, dispatch_id: str) -> None:
        self.pid = pid
        self.killed = False
        self._env = {"CURSOR_SDK_DISPATCH_ID": dispatch_id}

    def environ(self) -> dict[str, str]:
        return self._env

    def cmdline(self) -> list[str]:
        return ["cursor-sdk-bridge", "--pid", str(self.pid)]

    def exe(self) -> str:
        return "/usr/bin/cursor-sdk-bridge"

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0


class _Client:
    def __init__(self, *, fail_close: bool) -> None:
        self.fail_close = fail_close
        self.close_attempted = False

    def close(self) -> None:
        self.close_attempted = True
        if self.fail_close:
            raise RuntimeError("close failed")


def _install_bridge(
    monkeypatch: pytest.MonkeyPatch, dispatch_id: str, *, fail_close: bool
) -> tuple[_Bridge, _Client, list[tuple[str, dict[str, Any]]]]:
    proc = _Bridge(4242, dispatch_id)
    client = _Client(fail_close=fail_close)
    register_active_client(dispatch_id=dispatch_id, client=client)  # type: ignore[arg-type]
    captured: list[tuple[str, dict[str, Any]]] = []

    def _iter(_attrs: object = None) -> list[_Bridge]:
        return [] if proc.killed else [proc]

    def _record(signal: str, **payload: Any) -> None:
        captured.append((signal, payload))

    monkeypatch.setattr(orphan_mod.psutil, "process_iter", _iter)
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_events.record",
        _record,
    )
    return proc, client, captured


def _orphan_events(
    captured: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for signal, payload in captured:
        if signal != "frontier.sdk.worker.orphaned":
            continue
        body = dict(payload)
        body.pop("extra_payload", None)
        events.append(body)
    return events


@pytest.mark.asyncio
async def test_reclaim_running_holder_kills_env_matched_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abort success stamps bridge_aborted and the env-matched process is gone."""
    ledger = CursorDispatchLedger.instance()
    req = _req(dispatch_id="run-1", execution_id="e-run", message="run")
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(req),
        source_repo="/tmp/repo-reclaim-run",
    )
    ledger.mark_running(dispatch_id="run-1")
    proc, client, captured = _install_bridge(monkeypatch, "run-1", fail_close=False)
    real_force = park_mod.force_release_sdk_dispatch_slot

    async def _force(*, dispatch_id: str) -> bool:
        assert proc.killed is True
        assert client.close_attempted is True
        return await real_force(dispatch_id=dispatch_id)

    monkeypatch.setattr(park_mod, "force_release_sdk_dispatch_slot", _force)

    reclaimed = await reclaim_orphan_holder(ledger, dispatch_id="run-1")
    assert reclaimed.lease_key == "/tmp/repo-reclaim-run"
    assert reclaimed.resume_admitted is False
    assert proc.killed is True
    assert _orphan_events(captured) == [
        {
            "dispatch_id": "run-1",
            "thread_id": "t1",
            "execution_id": "e-run",
            "resolved_model": "composer-2.5",
            "timeout_s": 0.0,
            "bridge_aborted": True,
            "terminal_status": "failed",
        }
    ]
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("run-1",),
        ).fetchone()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_reclaim_parked_child_abort_error_still_reaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parked child: failed abort stamps False, and the bridge process is still gone."""
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-reclaim-park"
    parent = _req(dispatch_id="parent-o", execution_id="e-p", message="parent")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="parent-o")
    child = _req(
        dispatch_id="child-o",
        execution_id="e-c",
        message="child",
        nest_under="parent-o",
        thread_id="t-c",
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(child),
        source_repo=repo,
        nest_under="parent-o",
    )
    proc, client, captured = _install_bridge(monkeypatch, "child-o", fail_close=True)
    real_mark = ledger.mark_terminal

    def _mark(*, dispatch_id: str, terminal_status: str) -> str | None:
        if dispatch_id == "child-o":
            assert proc.killed is True
            assert client.close_attempted is True
        return real_mark(dispatch_id=dispatch_id, terminal_status=terminal_status)

    monkeypatch.setattr(ledger, "mark_terminal", _mark)

    reclaimed = await reclaim_orphan_holder(ledger, dispatch_id="parent-o")
    assert reclaimed.lease_key == repo
    assert reclaimed.resume_admitted is False
    assert proc.killed is True
    events = _orphan_events(captured)
    assert len(events) == 1
    assert events[0]["dispatch_id"] == "child-o"
    assert events[0]["bridge_aborted"] is False
    with _connect() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-o",),
        ).fetchone()
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_reclaim_completed_nest_child_resumes_instead_of_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breaks when park restore left the parent running and the nest child completed.

    That state used to emit ``frontier.sdk.worker.orphaned`` with ``timeout_s``
    0.0 and set the parent ``terminal_status`` to failed. Reclaim must admit
    ``resume_of`` so the child commit is harvested. A still-running child is
    the parked-child test above, which still fails that child.
    """
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-reclaim-completed-child"
    parent = _req(dispatch_id="parent-done", execution_id="e-p", message="parent")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="parent-done")
    child = _req(
        dispatch_id="child-done",
        execution_id="e-c",
        message="child",
        nest_under="parent-done",
        thread_id="t-c",
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(child),
        source_repo=repo,
        nest_under="parent-done",
    )
    ledger.mark_terminal(dispatch_id="child-done", terminal_status="completed")
    assert ledger.restore_from_park(parent_id="parent-done") == repo
    with _connect() as conn:
        restored = conn.execute(
            "SELECT status, park_child_dispatch_id, terminal_status "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("parent-done",),
        ).fetchone()
    assert restored["status"] == "running"
    assert restored["park_child_dispatch_id"] is None
    assert restored["terminal_status"] is None

    _proc, _client, captured = _install_bridge(
        monkeypatch, "parent-done", fail_close=False
    )
    admitted = AsyncMock(return_value=type("Resp", (), {"status_code": 200})())
    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_sdk.admit_cursor_dispatch",
        admitted,
    )

    class _Ctl:
        worker_id = "w"

    reclaimed = await reclaim_orphan_holder(
        ledger,
        dispatch_id="parent-done",
        controller=_Ctl(),
        cfg=object(),
    )
    assert reclaimed.resume_admitted is True
    assert reclaimed.lease_key is None
    assert _orphan_events(captured) == []
    admitted.assert_awaited()
    req = admitted.await_args.args[0]
    assert req.resume_of == "parent-done"
    assert req.dispatch_id == "parent-done-nr1"
    with _connect() as conn:
        parent_row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("parent-done",),
        ).fetchone()
        child_row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            ("child-done",),
        ).fetchone()
    assert parent_row["status"] == "cancelled"
    assert parent_row["terminal_status"] == "cancelled"
    assert child_row["status"] == "completed"


def _draining_admit() -> JSONResponse:
    """503 envelope a live drain returns from ``admit_cursor_dispatch``."""
    return JSONResponse(
        status_code=503,
        content={
            "code": "GIT_WORKER_DRAINING",
            "message": "git-integration-worker is draining (epoch=1)",
        },
    )


def _seed_restored_parent_with_completed_child(
    ledger: CursorDispatchLedger, *, parent_id: str, child_id: str, repo: str
) -> None:
    parent = _req(dispatch_id=parent_id, execution_id="e-p", message="parent")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id=parent_id)
    child = _req(
        dispatch_id=child_id,
        execution_id="e-c",
        message="child",
        nest_under=parent_id,
        thread_id="t-c",
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(child),
        source_repo=repo,
        nest_under=parent_id,
    )
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="completed")
    assert ledger.restore_from_park(parent_id=parent_id) == repo


def _stub_sweep_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the sweep on the orphan loop. Worktree reap hits the live hub."""

    async def _no_phantoms() -> list[str]:
        return []

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate.reclaim_cross_lane_phantom_holders",
        _no_phantoms,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_land_lease.reap_stale_land_leases",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.routes.cursor_sdk.reap_orphan_worktrees",
        lambda **_kwargs: type(
            "Sweep",
            (),
            {"reaped": 0, "salvaged": 0, "branches_retained": 0, "branches_gc": 0},
        )(),
    )


def _worker_cfg(tmp_path: Path):
    from services.git_integration_worker.config import WorkerConfig

    repo = tmp_path / "source"
    repo.mkdir()
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    return WorkerConfig(
        host="127.0.0.1",
        port=0,
        source_repo=repo,
        worktree_root=worktrees,
        dispatch_workspace=tmp_path / "dispatch_ws",
        green_gate_cmd=["true"],
    )


def _terminal_before_lease(
    captured: list[tuple[str, dict[str, Any]]], dispatch_id: str
) -> None:
    terminals = (
        "frontier.sdk.worker.orphaned",
        "frontier.sdk.worker.failed",
        "frontier.sdk.worker.cancelled",
        "frontier.sdk.worker.completed",
        "frontier.sdk.worker.timeout",
    )
    order = [
        signal
        for signal, payload in captured
        if payload.get("dispatch_id") == dispatch_id
        and signal in (*terminals, "frontier.sdk.worker.lease.released")
    ]
    assert "frontier.sdk.worker.lease.released" in order
    assert any(signal in terminals for signal in order)
    assert order.index("frontier.sdk.worker.lease.released") > min(
        idx for idx, signal in enumerate(order) if signal in terminals
    )


@pytest.mark.asyncio
async def test_reap_only_completed_nest_child_skips_resume_on_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a reap_only sweep admits resume_of during a drain.

    ``admit_cursor_dispatch`` is stubbed to 503 ``GIT_WORKER_DRAINING``.
    The sweep must not call it. The parent is failed on the ordinary reap
    path, and that worker terminal is published before ``lease.released``.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-reclaim-drain-skip"
    _seed_restored_parent_with_completed_child(
        ledger, parent_id="parent-drain", child_id="child-drain", repo=repo
    )
    _proc, _client, captured = _install_bridge(
        monkeypatch, "parent-drain", fail_close=False
    )
    response = _draining_admit()
    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "GIT_WORKER_DRAINING"
    admitted = AsyncMock(return_value=response)
    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", admitted)
    _stub_sweep_side_effects(monkeypatch)

    class _Ctl:
        worker_id = "w"
        worker_started_at = "2026-01-01T00:00:00+00:00"

        def is_draining(self) -> bool:
            return True

    await route_mod.reconcile_stale_leases(
        _Ctl(), reap_only=True, worker_cfg=_worker_cfg(tmp_path)
    )
    admitted.assert_not_awaited()
    _terminal_before_lease(captured, "parent-drain")
    with _connect() as conn:
        parent_row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("parent-drain",),
        ).fetchone()
    assert parent_row["status"] == "failed"
    assert parent_row["terminal_status"] == "failed"


@pytest.mark.asyncio
async def test_refused_nest_resume_emits_terminal_before_lease_released(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Breaks when a 503 refusal cancels the parent and skips the terminal.

    The admit is invoked and returns 503 ``GIT_WORKER_DRAINING``. The row
    is already cancelled, which used to hide ``frontier.sdk.worker.orphaned``.
    The sweeper must still publish that terminal before ``lease.released``,
    keyed on ``resume_admitted`` rather than ledger status.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-reclaim-drain-refuse"
    _seed_restored_parent_with_completed_child(
        ledger, parent_id="parent-refuse", child_id="child-refuse", repo=repo
    )
    _proc, _client, captured = _install_bridge(
        monkeypatch, "parent-refuse", fail_close=False
    )
    response = _draining_admit()
    admitted = AsyncMock(return_value=response)
    monkeypatch.setattr(route_mod, "admit_cursor_dispatch", admitted)
    _stub_sweep_side_effects(monkeypatch)

    async def _no_resume(**_kwargs: object) -> None:
        return None

    monkeypatch.setattr(route_mod, "_resume_parked_rows", _no_resume)
    monkeypatch.setattr(route_mod, "_resume_await_reply_rows", _no_resume)

    class _Ctl:
        worker_id = "w"
        worker_started_at = "2026-01-01T00:00:00+00:00"

        def is_draining(self) -> bool:
            return False

    await route_mod.reconcile_stale_leases(
        _Ctl(), reap_only=False, worker_cfg=_worker_cfg(tmp_path)
    )
    admitted.assert_awaited()
    assert json.loads(bytes(response.body))["code"] == "GIT_WORKER_DRAINING"
    _terminal_before_lease(captured, "parent-refuse")
    with _connect() as conn:
        parent_row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("parent-refuse",),
        ).fetchone()
    assert parent_row["status"] == "cancelled"
    assert parent_row["terminal_status"] == "cancelled"
