"""Tool-call budget meter steer latch and exhaustion parking."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_run_budget_meter import (
    maybe_deposit_budget_warn,
    maybe_park_budget_exhausted,
    merge_run_budget_control,
    read_run_budget_meter,
    try_claim_budget_warn_deposit,
)
from services.git_integration_worker.cursor_sdk_run_limits import (
    RUN_BUDGET_EXHAUSTED_REASON,
    run_limits_record_patch,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def _admit(ledger: CursorDispatchLedger, dispatch_id: str = "budget-meter-1") -> str:
    req = CursorDispatchRequest(
        thread_id="24958",
        model="cursor/grok-4.7",
        dispatch_id=dispatch_id,
        execution_id="exec-1",
        message="test",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent="cursor-sdk",
        resolved_model="grok-4.7",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=req.thread_id,
            model_id="grok-4.7",
        ),
        contract="implement",
        source_repo="/repo",
        lease_key="/repo",
        work_key="todo:run-budget-meter",
        source_ref="todo:run-budget-meter",
    )
    ledger.mark_running(dispatch_id=dispatch_id)
    return dispatch_id


def test_warn_once_latched() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = _admit(ledger)
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch=run_limits_record_patch(
            {
                "tool_call_budget": 20,
                "warn_at": 15,
                "upstream_tool_call_limits": "unknown",
                "native_helper_enforcement": "enforced",
            }
        ),
    )
    def _row() -> dict[str, str]:
        with ledger._connect() as conn:
            rec = conn.execute(
                "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return {
            "dispatch_id": dispatch_id,
            "thread_id": "24958",
            "record_json": str(rec["record_json"] or ""),
        }

    with patch(
        "services.git_integration_worker.cursor_sdk_run_budget_meter.deposit_steer_directive",
        return_value=type(
            "D",
            (),
            {
                "dispatch_id": dispatch_id,
                "entry_id": "e1",
                "authority_turn_id": "a1",
                "spool_path": "",
            },
        )(),
    ):
        deposit = maybe_deposit_budget_warn(
            _row(),
            tool_call_count=16,
            submitted_id="test",
            ledger=ledger,
        )
    assert deposit is not None
    assert (
        maybe_deposit_budget_warn(
            _row(),
            tool_call_count=17,
            submitted_id="test2",
            ledger=ledger,
        )
        is None
    )
    second, _ = try_claim_budget_warn_deposit(
        dispatch_id=dispatch_id, claim_token="t2", tool_call_count=17, ledger=ledger
    )
    assert second == "latched"


def test_exhaustion_parks_budget_exhausted() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = _admit(ledger, dispatch_id="budget-meter-2")
    ledger.merge_record_json(
        dispatch_id=dispatch_id,
        patch=run_limits_record_patch(
            {
                "tool_call_budget": 5,
                "warn_at": 4,
                "upstream_tool_call_limits": "unknown",
                "native_helper_enforcement": "enforced",
            }
        ),
    )
    with ledger._connect() as conn:
        rec = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    row = {"dispatch_id": dispatch_id, "record_json": str(rec["record_json"] or "")}
    with patch(
        "services.git_integration_worker.cursor_sdk_run_budget_meter.signal_park",
        return_value=type("P", (), {"ok": True})(),
    ):
        result = maybe_park_budget_exhausted(row, tool_call_count=5)
    assert result is not None
    with ledger._connect() as conn:
        rec = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    reading = read_run_budget_meter(str(rec["record_json"]))
    assert reading.budget_exhausted is True
    assert RUN_BUDGET_EXHAUSTED_REASON == "tool-call-budget-exhausted"


def test_stream_tool_call_count_persists() -> None:
    ledger = CursorDispatchLedger.instance()
    dispatch_id = _admit(ledger, dispatch_id="budget-meter-3")
    merge_run_budget_control(
        ledger, dispatch_id=dispatch_id, patch={"stream_tool_call_count": 3}
    )
    with ledger._connect() as conn:
        rec = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    reading = read_run_budget_meter(str(rec["record_json"]))
    assert reading.stream_tool_call_count == 3