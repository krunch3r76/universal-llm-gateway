"""GIW restart survivors must emit ES terminals so the dispatch board can close rows."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    LedgerRow,
    _connect,
)
from services.git_integration_worker.cursor_sdk_events import (
    register_cursor_sdk_event_publisher,
)
from services.git_integration_worker.cursor_sdk_orphan import (
    BridgeReapResult,
    is_cursor_sdk_bridge_process,
    reap_orphan_bridge_os,
)
from services.git_integration_worker.cursor_sdk_restart_orphan import (
    emit_restart_survivor_terminal,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import ReapSweepResult
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _admit_running(
    ledger: CursorDispatchLedger,
    *,
    dispatch_id: str,
    thread_id: str,
    execution_id: str,
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=execution_id,
        message="restart survivor probe",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
    )
    ledger.mark_running(dispatch_id=dispatch_id)


def test_emit_restart_survivor_terminal() -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    def _capture(signal: str, payload: dict[str, object]) -> None:
        emitted.append((signal, payload))

    register_cursor_sdk_event_publisher(_capture)
    orphan = LedgerRow(
        dispatch_id="d-surv",
        thread_id="6204",
        execution_id="e-surv",
        caller_agent=None,
        resolved_model="cursor/composer-2.5",
        state_root=None,
        sdk_agent_id=None,
        sdk_run_id=None,
        status="running",
        started_at="2026-07-28T17:50:19Z",
        last_heartbeat_at="2026-07-28T17:55:34Z",
    )
    emit_restart_survivor_terminal(orphan)
    assert len(emitted) == 1
    signal, payload = emitted[0]
    assert signal == "frontier.sdk.worker.orphaned"
    assert payload["dispatch_id"] == "d-surv"
    assert payload["thread_id"] == "6204"
    assert payload["execution_id"] == "e-surv"
    assert payload["bridge_aborted"] is False
    assert payload["terminal_status"] == "failed"

    register_cursor_sdk_event_publisher(_capture)
    emit_restart_survivor_terminal(orphan, bridge_aborted=True)
    assert emitted[-1][1]["bridge_aborted"] is True


def test_is_cursor_sdk_bridge_process_rejects_non_bridge_daemon() -> None:
    class _Proc:
        pid = 999

        @staticmethod
        def cmdline() -> list[str]:
            return ["some-daemon", "--serve"]

        @staticmethod
        def exe() -> str:
            return "/usr/bin/some-daemon"

    assert not is_cursor_sdk_bridge_process(_Proc())  # type: ignore[arg-type]


def test_is_cursor_sdk_bridge_process_accepts_cursor_sdk_bridge() -> None:
    class _Proc:
        pid = 1000

        @staticmethod
        def cmdline() -> list[str]:
            return ["/opt/cursor-sdk-bridge", "--workspace", "/tmp/ws"]

        @staticmethod
        def exe() -> str:
            return "/opt/cursor-sdk-bridge"

    assert is_cursor_sdk_bridge_process(_Proc())  # type: ignore[arg-type]


def test_reap_orphan_bridge_os_skips_non_bridge_env_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch_id = "d-reap-skip"

    class _NonBridgeProc:
        pid = 1001

        @staticmethod
        def environ() -> dict[str, str]:
            return {"CURSOR_SDK_DISPATCH_ID": dispatch_id}

        @staticmethod
        def cmdline() -> list[str]:
            return ["some-daemon"]

        @staticmethod
        def exe() -> str:
            return "/usr/bin/some-daemon"

    class _BridgeProc:
        pid = 1002
        killed = False

        @staticmethod
        def environ() -> dict[str, str]:
            return {"CURSOR_SDK_DISPATCH_ID": dispatch_id}

        @staticmethod
        def cmdline() -> list[str]:
            return ["cursor-sdk-bridge"]

        @staticmethod
        def exe() -> str:
            return "/opt/cursor-sdk-bridge"

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float = 0) -> None:
            del timeout

    bridge = _BridgeProc()

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.psutil.process_iter",
        lambda *args, **kwargs: [_NonBridgeProc(), bridge],
    )
    result = reap_orphan_bridge_os(dispatch_id)
    assert isinstance(result, BridgeReapResult)
    assert result.bridge_aborted is True
    assert bridge.killed is True


@pytest.mark.asyncio
async def test_startup_ledger_reconcile_emits_restart_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[str] = []

    def _capture(signal: str, payload: dict[str, object]) -> None:
        del payload
        emitted.append(signal)

    register_cursor_sdk_event_publisher(_capture)
    ledger = CursorDispatchLedger.instance()
    _admit_running(
        ledger,
        dispatch_id="e599802b607b-246da3fe",
        thread_id="6204",
        execution_id="e00db55a-f362-4fbd-a6b7-2624fdfa5403",
    )
    assert ledger.running_orphans()

    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="restart-test",
        pid=0,
        worker_started_at="test",
    )
    app = SimpleNamespace(
        state=SimpleNamespace(
            worker_config=SimpleNamespace(
                source_repo="/tmp/repo",
                worktree_root="/tmp/worktrees",
            ),
            admission_controller=controller,
        )
    )
    monkeypatch.setattr(route_mod, "prune_stale_dispatch_homes", lambda: 0)
    monkeypatch.setattr(
        route_mod,
        "reap_orphan_worktrees",
        lambda **_k: ReapSweepResult(),
    )
    monkeypatch.setattr(
        route_mod,
        "reap_orphan_bridge_os",
        lambda dispatch_id: BridgeReapResult(bridge_aborted=True),
    )
    monkeypatch.setattr(
        route_mod,
        "release_or_restore_for_child",
        AsyncMock(return_value="released"),
    )
    monkeypatch.setattr(
        route_mod,
        "_promote_queued_for_lease",
        AsyncMock(return_value=None),
    )

    await route_mod.startup_ledger_reconcile(app)

    assert "frontier.sdk.worker.orphaned" in emitted
    with _connect() as conn:
        row = conn.execute(
            "SELECT status, terminal_status FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            ("e599802b607b-246da3fe",),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["terminal_status"] == "failed"
