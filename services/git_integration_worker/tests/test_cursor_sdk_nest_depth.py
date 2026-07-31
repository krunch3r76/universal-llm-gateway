"""Nest depth precheck + product-path nest_under edge cases."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_nest_depth import (
    MAX_NEST_DEPTH,
    NestDepthExceeded,
    NestParentNotLive,
    park_stack_depth,
)
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


def _admit_chain(
    ledger: CursorDispatchLedger, repo: str, ids: list[str]
) -> None:
    """Admit a linear nest chain root → … → deepest live holder."""
    parent_id: str | None = None
    for i, dispatch_id in enumerate(ids):
        req = _req(
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message=f"msg-{i}",
            thread_id=f"t-{dispatch_id}",
        )
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(req),
            source_repo=repo,
            nest_under=parent_id,
        )
        ledger.mark_running(dispatch_id=dispatch_id)
        parent_id = dispatch_id


def test_self_nest_rejects() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-self"
    child = _req(
        dispatch_id="self-nest-id",
        execution_id="exec-self",
        message="child",
        thread_id="t-self",
    )
    with pytest.raises(NestDepthExceeded):
        ledger.admit(
            req=child,
            fingerprint=ledger.fingerprint(child),
            execution_id=child.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(child),
            source_repo=repo,
            nest_under="self-nest-id",
        )


def test_illegal_parent_not_live_rejects() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-illegal"
    root = _req(dispatch_id="root-live")
    ledger.admit(
        req=root,
        fingerprint=ledger.fingerprint(root),
        execution_id=root.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=_admission(root),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="root-live")
    child = _req(
        dispatch_id="child-bad",
        execution_id="exec-bad",
        message="child",
        thread_id="t-bad",
    )
    with pytest.raises(NestParentNotLive):
        ledger.admit(
            req=child,
            fingerprint=ledger.fingerprint(child),
            execution_id=child.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(child),
            source_repo=repo,
            nest_under="unknown-parent",
        )


def test_depth_eleven_rejects_without_new_row() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-cap"
    chain = [f"depth-{i}" for i in range(MAX_NEST_DEPTH + 1)]
    _admit_chain(ledger, repo, chain)
    overflow = _req(
        dispatch_id="depth-overflow",
        execution_id="exec-overflow",
        message="overflow",
        thread_id="t-overflow",
    )
    with pytest.raises(NestDepthExceeded):
        ledger.admit(
            req=overflow,
            fingerprint=ledger.fingerprint(overflow),
            execution_id=overflow.execution_id,
            caller_agent=None,
            resolved_model="composer-2.5",
            admission=_admission(overflow),
            source_repo=repo,
            nest_under=chain[-1],
        )
    row = ledger._connect().execute(  # noqa: SLF001
        "SELECT 1 FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        ("depth-overflow",),
    ).fetchone()
    assert row is None


def test_stack_depth_two_lifo_restore() -> None:
    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-lifo"
    _admit_chain(ledger, repo, ["root-a", "child-b", "child-c"])
    assert ledger.find_parked_parent_for_child(child_id="child-c") == (
        "child-b",
        repo,
    )
    assert ledger.find_parked_parent_for_child(child_id="child-b") == (
        "root-a",
        repo,
    )
    ledger.mark_terminal(dispatch_id="child-c", terminal_status="completed")
    assert ledger.restore_from_park(parent_id="child-b") == repo
    ledger.mark_terminal(dispatch_id="child-b", terminal_status="completed")
    assert ledger.restore_from_park(parent_id="root-a") == repo
