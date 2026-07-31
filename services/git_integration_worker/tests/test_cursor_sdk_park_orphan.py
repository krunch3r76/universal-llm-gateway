"""Park-orphan L1: child failure restore, unified orphan scan, queue stall alarm."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_events import (
    reset_terminal_emitted_registry,
)
from services.git_integration_worker.cursor_sdk_park import (
    orphan_holders,
    queue_stall_lease_keys,
    release_or_restore_for_child,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from services.git_integration_worker.routes import cursor_sdk as route_mod


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


def _park_parent_child(
    ledger: CursorDispatchLedger, *, repo: str
) -> tuple[str, str]:
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
    return "parent-o", "child-o"


@pytest.mark.asyncio
async def test_child_failed_restores_parked_parent() -> None:
    """Change 1: child terminal failed must restore parent via park path."""
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-child-fail"
    parent_id, child_id = _park_parent_child(ledger, repo=repo)

    ledger.mark_terminal(dispatch_id=child_id, terminal_status="failed")
    disposition = await release_or_restore_for_child(dispatch_id=child_id)
    assert disposition == "restored"

    with _connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "running"
    assert ledger.find_parked_parent_for_child(child_id=child_id) is None


@pytest.mark.asyncio
async def test_mark_terminal_and_promote_child_failed_restores_parent() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-mark-terminal"
    parent_id, child_id = _park_parent_child(ledger, repo=repo)
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="test-worker",
        pid=0,
        worker_started_at="test",
    )
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="failed")
    await route_mod._mark_terminal_and_promote(
        dispatch_id=child_id,
        terminal_status="failed",
        controller=controller,
        emit_tag="CURSOR_TEST_PARK_CHILD",
    )
    with _connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "running"


def test_parked_waiting_foreign_instance_orphan_reaped() -> None:
    """Change 2: parked parent + terminal child on foreign worker is orphan."""
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-cross-instance"
    parent_id, child_id = _park_parent_child(ledger, repo=repo)
    with _connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET worker_instance=? WHERE dispatch_id=?",
            ("dead-worker", parent_id),
        )
    ledger.mark_terminal(dispatch_id=child_id, terminal_status="failed")

    assert parent_id in orphan_holders(ledger)
    keys = ledger.startup_reconcile(worker_instance="live-worker")
    assert repo in keys
    with _connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent["status"] == "running"


def test_queue_stall_alarm_emits_on_empty_workers() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-stall"
    parent = _req(dispatch_id="held", execution_id="e-h", message="held")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
        contract="implement",
    )
    sibling = _req(dispatch_id="queued-1", execution_id="e-q", message="q", thread_id="t2")
    queued = ledger.admit(
        req=sibling,
        fingerprint=ledger.fingerprint(sibling),
        execution_id=sibling.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(sibling),
        source_repo=repo,
        contract="implement",
    )
    assert queued is not None and queued.status == "queued"
    ledger.mark_terminal(dispatch_id="held", terminal_status="failed")

    assert repo in queue_stall_lease_keys(ledger)

    emitted: list[str] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append(signal)

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record", side_effect=_capture
    ):
        from services.git_integration_worker.cursor_sdk_events import (
            emit_write_lease_queue_stalled,
        )

        emit_write_lease_queue_stalled(source_repo=repo)

    assert emitted == ["frontier.sdk.worker.lease.queue_stalled"]
