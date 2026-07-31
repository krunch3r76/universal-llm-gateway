"""A1 falsifier: child finally-release must not wake sibling while parent parked."""

from __future__ import annotations

import asyncio

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    sdk_dispatch_gate_holders,
    sdk_dispatch_gate_stats,
)
from services.git_integration_worker.cursor_sdk_park import (
    release_or_restore_for_child,
    transfer_capacity_after_park,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)
from universal_concurrency import FifoCapacityGate


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_CONCURRENCY", "1")
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "parent-a1",
        "execution_id": "exec-a1",
        "message": "parent",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def _admission(req: CursorDispatchRequest) -> CursorDispatchResponse:
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=req.dispatch_id,
        thread_id=req.thread_id,
        model_id="c",
    )


@pytest.mark.asyncio
async def test_a1_finally_release_restores_parent_not_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sibling blocked in acquire must NOT win when child restores to parked parent."""
    # Isolate capacity on a fresh gate so process-global _GATE state cannot leak.
    isolated = FifoCapacityGate(limit=1, gate_id="a1-test")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_gate._GATE", isolated
    )

    ledger = CursorDispatchLedger.instance()
    repo = "/tmp/repo-a1"
    parent = _req(dispatch_id="parent-a1", execution_id="e-p", message="p")
    ledger.admit(
        req=parent,
        fingerprint=ledger.fingerprint(parent),
        execution_id=parent.execution_id,
        caller_agent=None,
        resolved_model="c",
        admission=_admission(parent),
        source_repo=repo,
    )
    ledger.mark_running(dispatch_id="parent-a1")
    await acquire_sdk_dispatch_slot(dispatch_id="parent-a1")

    child = _req(
        dispatch_id="child-a1",
        execution_id="e-c",
        message="c",
        nest_under="parent-a1",
        thread_id="t-c",
    )
    ledger.admit(
        req=child,
        fingerprint=ledger.fingerprint(child),
        execution_id=child.execution_id,
        caller_agent=None,
        resolved_model="c",
        admission=_admission(child),
        source_repo=repo,
        nest_under="parent-a1",
    )
    await transfer_capacity_after_park(
        parent_id="parent-a1", child_id="child-a1", source_repo=repo
    )
    assert sdk_dispatch_gate_holders() == frozenset({"child-a1"})

    sibling_got = asyncio.Event()

    async def sibling_acquire() -> None:
        await acquire_sdk_dispatch_slot(dispatch_id="sibling-a1")
        sibling_got.set()

    task = asyncio.create_task(sibling_acquire())
    await asyncio.sleep(0.05)
    assert sdk_dispatch_gate_stats()["queued"] == 1
    assert not sibling_got.is_set()

    disposition = await release_or_restore_for_child(dispatch_id="child-a1")
    assert disposition == "restored"
    await asyncio.sleep(0.05)
    assert not sibling_got.is_set()
    assert sdk_dispatch_gate_holders() == frozenset({"parent-a1"})

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await isolated.cancel("sibling-a1")
