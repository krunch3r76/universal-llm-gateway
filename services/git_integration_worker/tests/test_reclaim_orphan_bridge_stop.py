"""Orphan reclaim stops the billing bridge before the row is failed (a:36215)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

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

    key = await reclaim_orphan_holder(ledger, dispatch_id="run-1")
    assert key == "/tmp/repo-reclaim-run"
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

    key = await reclaim_orphan_holder(ledger, dispatch_id="parent-o")
    assert key == repo
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

    key = await reclaim_orphan_holder(
        ledger,
        dispatch_id="parent-done",
        controller=_Ctl(),
        cfg=object(),
    )
    assert key is None
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
