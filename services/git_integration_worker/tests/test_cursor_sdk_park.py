"""Park/restore ledger helpers + nest_under admit (PARK-RESTORE-DUAL)."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
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


def test_nest_under_parks_parent_and_admits_child() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-park"
    parent = _req(dispatch_id="parent-1", execution_id="e-p", message="parent")
    assert (
        ledger.admit(
            req=parent,
            fingerprint=ledger.fingerprint(parent),
            execution_id=parent.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(parent),
            source_repo=repo,
        )
        is None
    )
    ledger.mark_running(dispatch_id="parent-1")

    child = _req(
        dispatch_id="child-1",
        execution_id="e-c",
        message="child",
        nest_under="parent-1",
        thread_id="t-child",
    )
    assert (
        ledger.admit(
            req=child,
            fingerprint=ledger.fingerprint(child),
            execution_id=child.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(child),
            source_repo=repo,
            nest_under="parent-1",
        )
        is None
    )

    parked = ledger.find_parked_parent_for_child(child_id="child-1")
    assert parked is not None
    assert parked[0] == "parent-1"
    assert ledger.has_parked_parent(source_repo=repo) is True
    assert (
        ledger.promote_next_queued(source_repo=repo, worker_instance="w") is None
    )


def test_naive_nest_without_park_still_queues() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-queue"
    parent = _req(dispatch_id="parent-2", execution_id="e-p2", message="parent")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="parent-2")

    sibling = _req(
        dispatch_id="sib-1",
        execution_id="e-s",
        message="sibling",
        thread_id="t-sib",
    )
    cached = ledger.admit(
        req=sibling,
        fingerprint=ledger.fingerprint(sibling),
        execution_id=sibling.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(sibling),
        source_repo=repo,
    )
    assert cached is not None
    assert cached.status == "queued"


def test_restore_from_park_clears_park_and_unblocks_promote() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-restore"
    parent = _req(dispatch_id="parent-3", execution_id="e-p3", message="parent")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="parent-3")
    child = _req(
        dispatch_id="child-3",
        execution_id="e-c3",
        message="child",
        nest_under="parent-3",
        thread_id="t-c3",
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(child),
        source_repo=repo,
        nest_under="parent-3",
    )
    sibling = _req(
        dispatch_id="sib-3",
        execution_id="e-s3",
        message="sib",
        thread_id="t-s3",
    )
    queued = ledger.admit(
        req=sibling,
        fingerprint=ledger.fingerprint(sibling),
        execution_id=sibling.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(sibling),
        source_repo=repo,
    )
    assert queued is not None and queued.status == "queued"

    ledger.mark_terminal(dispatch_id="child-3", terminal_status="completed")
    assert ledger.restore_from_park(parent_id="parent-3") == repo
    assert ledger.find_parked_parent_for_child(child_id="child-3") is None
    # Parent still running — promote must stay blocked until parent terminals.
    assert (
        ledger.promote_next_queued(source_repo=repo, worker_instance="w") is None
    )
    ledger.mark_terminal(dispatch_id="parent-3", terminal_status="completed")
    promoted = ledger.promote_next_queued(source_repo=repo, worker_instance="w")
    assert promoted is not None
    assert promoted.dispatch_id == "sib-3"
