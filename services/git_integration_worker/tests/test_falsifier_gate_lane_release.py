"""Acquire/release lane symmetry for the cursor-sdk capacity gates.

Regression cover for the operator-lane slot leak: acquire resolved the lane from
``caller_agent`` while release resolved it from ``dispatch_id`` alone, which
recognizes only the ``auto-`` prefix. An IDE dispatch therefore took an operator
slot and released a standard one, leaking the operator slot permanently — three
such dispatches wedge the lane (limit 3) until the worker restarts.
"""

from __future__ import annotations

import asyncio

import pytest

from services.git_integration_worker import cursor_sdk_gate
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_gate import (
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot,
    sdk_dispatch_gate_stats,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_IDE_DISPATCH_ID = "d40677a34a06-f3af9df4"
"""Shaped like a real IDE dispatch id — notably NOT ``auto-`` prefixed."""


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _admit_ide_dispatch() -> None:
    """Record an operator-lane (IDE seat) dispatch in the ledger."""
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="6350",
        model="cursor/composer-2.5",
        dispatch_id=_IDE_DISPATCH_ID,
        execution_id=f"exec-{_IDE_DISPATCH_ID}",
        message="hello",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor",
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=req.dispatch_id,
            thread_id=req.thread_id,
            model_id="composer-2.5",
        ),
        source_repo="/repo",
        lease_key=None,
        contract="implement",
        worker_instance="worker-a",
        nest_under=None,
    )


def _operator_active() -> int:
    return int(sdk_dispatch_gate_stats(lane="operator")["active"])


def _standard_active() -> int:
    return int(sdk_dispatch_gate_stats(lane="standard")["active"])


def test_operator_slot_released_when_release_knows_only_dispatch_id() -> None:
    """Release must free the SAME lane acquire took, given only ``dispatch_id``."""
    _admit_ide_dispatch()

    async def exercise() -> None:
        await acquire_sdk_dispatch_slot(
            dispatch_id=_IDE_DISPATCH_ID, caller_agent="cursor", timeout=5
        )
        assert _operator_active() == 1, "IDE dispatch must occupy the operator lane"
        assert _standard_active() == 0

        # The release path in production has no caller_agent in scope.
        await release_sdk_dispatch_slot(dispatch_id=_IDE_DISPATCH_ID)

    asyncio.run(exercise())

    assert _operator_active() == 0, "operator slot leaked — lane wedges after 3 of these"
    assert _standard_active() == 0


def test_ledger_lane_lookup_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralizing the ledger lookup reproduces the leak — the pre-fix behavior.

    Guards against the lookup being dropped again: without it, lane resolution on
    release sees only a non-``auto-`` id, picks the standard gate, and the operator
    slot is never returned.
    """
    _admit_ide_dispatch()
    monkeypatch.setattr(
        cursor_sdk_gate, "_caller_agent_for_dispatch", lambda dispatch_id: None
    )

    async def exercise() -> None:
        await acquire_sdk_dispatch_slot(
            dispatch_id=_IDE_DISPATCH_ID, caller_agent="cursor", timeout=5
        )
        assert _operator_active() == 1
        await release_sdk_dispatch_slot(dispatch_id=_IDE_DISPATCH_ID)

    asyncio.run(exercise())

    assert _operator_active() == 1, (
        "expected the pre-fix leak to reproduce without the ledger lane lookup"
    )

    async def cleanup() -> None:
        monkeypatch.undo()
        await release_sdk_dispatch_slot(dispatch_id=_IDE_DISPATCH_ID)

    asyncio.run(cleanup())
    assert _operator_active() == 0
