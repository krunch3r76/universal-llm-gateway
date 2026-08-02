"""Cross-lane park/restore gate correctness (item 12 / AC-12a–c).

Replays attempt-1 shape: operator parent ``auto-*`` nests standard child
``{uuid}-{hex8}``; restore must not install the operator id on the standard gate.
"""

from __future__ import annotations

import asyncio

import pytest
from universal_concurrency import CrossLaneTransferError, TransferHolderError

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_gate import (
    _OPERATOR_GATE,
    _STANDARD_GATE,
    acquire_sdk_dispatch_slot,
    reclaim_cross_lane_phantom_holders,
    release_sdk_dispatch_slot,
    sdk_dispatch_gate_holders,
    sdk_dispatch_gate_stats,
    sdk_dispatch_lane,
    transfer_sdk_dispatch_slot,
)
from services.git_integration_worker.cursor_sdk_park import (
    release_or_restore_for_child,
    transfer_capacity_after_park,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_PARENT = "auto-4ef000000001"
_CHILD = "d40677a34a06-f3af9df4"


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    for gate in (_STANDARD_GATE, _OPERATOR_GATE):
        gate._holders.clear()
        gate._active_count = 0
        gate._waiters.clear()
    yield
    CursorDispatchLedger._instance = None
    for gate in (_STANDARD_GATE, _OPERATOR_GATE):
        gate._holders.clear()
        gate._active_count = 0
        gate._waiters.clear()


def _admit(*, dispatch_id: str, nest_under: str | None = None) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="6655",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="nested",
        nest_under=nest_under,
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-auto" if dispatch_id.startswith("auto-") else None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo="/repo",
        lease_key="/repo",
        contract="implement",
        worker_instance="worker-a",
        nest_under=nest_under,
    )


@pytest.mark.asyncio
async def test_ac12a_cross_lane_restore_leaves_standard_gate_clean() -> None:
    """Attempt-1 replay: park on operator lane, restore must not phantom on standard."""
    _admit(dispatch_id=_PARENT)
    _admit(dispatch_id=_CHILD, nest_under=_PARENT)

    assert sdk_dispatch_lane(dispatch_id=_PARENT) == "operator"
    assert sdk_dispatch_lane(dispatch_id=_CHILD) == "operator"

    await acquire_sdk_dispatch_slot(dispatch_id=_PARENT, caller_agent="cursor-auto")
    await transfer_capacity_after_park(
        parent_id=_PARENT, child_id=_CHILD, source_repo="/repo"
    )
    assert sdk_dispatch_gate_holders(lane="operator") == frozenset({_CHILD})
    assert sdk_dispatch_gate_holders(lane="standard") == frozenset()

    await acquire_sdk_dispatch_slot(dispatch_id=_CHILD)
    disposition = await release_or_restore_for_child(dispatch_id=_CHILD)
    assert disposition == "restored"

    assert sdk_dispatch_gate_holders(lane="standard") == frozenset()
    assert sdk_dispatch_gate_holders(lane="operator") == frozenset({_PARENT})


@pytest.mark.asyncio
async def test_ac12a_transfer_rejects_cross_lane_restore() -> None:
    """Restore must not install operator parent on standard gate (attempt-1 defect)."""
    _STANDARD_GATE._holders.add(_CHILD)
    _STANDARD_GATE._active_count = 1

    with pytest.raises(CrossLaneTransferError):
        await transfer_sdk_dispatch_slot(from_id=_CHILD, to_id=_PARENT)

    assert _PARENT not in _STANDARD_GATE.holders


@pytest.mark.asyncio
async def test_ac12b_reclaim_cross_lane_phantom_holders() -> None:
    """Misplaced operator id on standard gate is force-released."""
    await acquire_sdk_dispatch_slot(dispatch_id=_CHILD)
    _STANDARD_GATE._holders.discard(_CHILD)
    _STANDARD_GATE._holders.add(_PARENT)
    _STANDARD_GATE._active_count = 1

    assert int(sdk_dispatch_gate_stats(lane="standard")["active"]) == 1

    reclaimed = await reclaim_cross_lane_phantom_holders()
    assert reclaimed == [_PARENT]
    assert sdk_dispatch_gate_holders(lane="standard") == frozenset()
    assert int(sdk_dispatch_gate_stats(lane="standard")["active"]) == 0


@pytest.mark.asyncio
async def test_transfer_raises_when_from_id_not_holder() -> None:
    with pytest.raises(TransferHolderError):
        await transfer_sdk_dispatch_slot(from_id="missing", to_id=_PARENT)


@pytest.mark.asyncio
async def test_nested_child_inherits_parent_lane_for_release() -> None:
    _admit(dispatch_id=_PARENT)
    _admit(dispatch_id=_CHILD, nest_under=_PARENT)
    await acquire_sdk_dispatch_slot(dispatch_id=_CHILD)
    assert sdk_dispatch_gate_holders(lane="operator") == frozenset({_CHILD})
    await release_sdk_dispatch_slot(dispatch_id=_CHILD)
    assert sdk_dispatch_gate_holders(lane="operator") == frozenset()
