"""Registry work_status override at cursor-sdk closeout (friction:41528 AC8)."""

from __future__ import annotations

import json

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_work_registry_admit import (
    record_dispatch_registry_best_effort,
)

pytestmark = pytest.mark.offline


@pytest.fixture
def ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> CursorDispatchLedger:
    db = tmp_path / "closeout-registry.sqlite"
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(db))
    CursorDispatchLedger._instance = None
    return CursorDispatchLedger.instance()


def _stamp_registry_row(ledger: CursorDispatchLedger, dispatch_id: str) -> None:
    record_dispatch_registry_best_effort(
        dispatch_id=dispatch_id,
        thread_id="21792",
        caller_agent="cursor-sdk",
        seat_name="cursor-sdk",
        address={
            "kind": "cursor_sdk",
            "dispatch_id": dispatch_id,
            "worker_thread": "21792",
        },
        ledger=ledger,
    )


def test_partial_closeout_stamps_partial_not_landed(ledger: CursorDispatchLedger) -> None:
    dispatch_id = "partial-closeout-1"
    _stamp_registry_row(ledger, dispatch_id)
    ledger.mark_terminal(
        dispatch_id=dispatch_id,
        terminal_status="completed",
        registry_work_status="partial",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("work_status") == "partial"


def test_complete_closeout_stamps_landed(ledger: CursorDispatchLedger) -> None:
    dispatch_id = "complete-closeout-1"
    _stamp_registry_row(ledger, dispatch_id)
    ledger.mark_terminal(
        dispatch_id=dispatch_id,
        terminal_status="completed",
        registry_work_status="landed",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("work_status") == "landed"


def test_failed_closeout_status_stamps_abandoned(ledger: CursorDispatchLedger) -> None:
    dispatch_id = "failed-closeout-1"
    _stamp_registry_row(ledger, dispatch_id)
    ledger.mark_terminal(
        dispatch_id=dispatch_id,
        terminal_status="completed",
        registry_work_status="abandoned",
    )
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    rec = json.loads(row["record_json"])
    assert rec.get("work_status") == "abandoned"
