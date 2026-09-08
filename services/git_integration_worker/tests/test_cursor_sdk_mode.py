"""Tests for cursor-sdk sdk_mode resolution and plan closeout gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_mode import (
    apply_plan_mode_closeout_gate,
    enforce_plan_read_only,
    resolve_sdk_mode,
    validate_sdk_mode_at_admit,
)
from services.git_integration_worker.cursor_sdk_plan_handoff_witness import (
    plan_handoff_for_conductor,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "d1",
        "execution_id": "e1",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def test_resolve_sdk_mode_explicit_plan() -> None:
    req = _req(sdk_mode="plan")
    assert (
        resolve_sdk_mode(req, contract="consult", packet_text="", effective_read_only=False)
        == "plan"
    )


def test_resolve_sdk_mode_packet_plan_line() -> None:
    req = _req(message="---\nsdk_mode: plan\n---\nbody")
    assert (
        resolve_sdk_mode(req, contract="none", packet_text=req.message, effective_read_only=False)
        == "plan"
    )


def test_resolve_sdk_mode_read_only_consult_defaults_plan() -> None:
    req = _req()
    assert (
        resolve_sdk_mode(req, contract="consult", packet_text="", effective_read_only=True)
        == "plan"
    )


def test_resolve_sdk_mode_implement_class_defaults_agent() -> None:
    req = _req()
    assert (
        resolve_sdk_mode(req, contract="implement", packet_text="", effective_read_only=False)
        == "agent"
    )


def test_validate_plan_implement_conflict() -> None:
    assert (
        validate_sdk_mode_at_admit("plan", contract="implement")
        == "sdk_mode=plan is incompatible with contract=implement (implement-class dispatches must use agent mode)"
    )


def test_enforce_plan_read_only_forces_true() -> None:
    assert enforce_plan_read_only("plan", False) is True


def test_apply_plan_mode_closeout_gate_blocks_land_and_sets_verdict() -> None:
    status, work_outcome, landed, deviations, verdict = apply_plan_mode_closeout_gate(
        sdk_mode="plan",
        status=CloseoutStatus.COMPLETE,
        work_outcome=WorkOutcome.SHIPPED,
        landed=True,
        deviations=[],
        artifact_paths=["cortex://notes/spec.md"],
        offgit_deliverable_uris=None,
    )
    assert status == CloseoutStatus.PARTIAL
    assert work_outcome == WorkOutcome.UNVERIFIED
    assert landed is None
    assert verdict == "PLAN_COMPLETE"
    assert "plan:land_claim_forbidden" in deviations


def test_plan_handoff_witness_reads_plan_child_from_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ledger reader returns nest_implement_hint for terminal plan child."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    conductor_id = "12345678-abcd-1234-abcd-123456789abc"
    parent = CursorDispatchRequest(
        thread_id="10363",
        model="cursor/composer-2.5",
        dispatch_id=conductor_id,
        execution_id="exec-parent",
        message="conductor",
    )
    child = CursorDispatchRequest(
        thread_id="10363",
        model="cursor/composer-2.5",
        dispatch_id="plan-child-1",
        execution_id="exec-child",
        message="plan",
    )
    for req, contract in ((parent, "conductor"), (child, "recon")):
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
            contract=contract,
            source_repo=str(tmp_path),
            lease_key=str(tmp_path),
        )
    hint = {
        "verdict": "PLAN_COMPLETE",
        "density_triage": "implement_ready",
        "thread_id": "10363",
        "dispatch_id": "plan-child-1",
        "contract": "implement",
        "sdk_mode": "agent",
    }
    closeout_json = json.dumps({"nest_implement_hint": hint})
    ledger.merge_record_json(
        dispatch_id="plan-child-1",
        patch={
            "nest_under": conductor_id,
            "closeout_body": closeout_json,
            "sdk_mode": "plan",
        },
    )
    ledger.mark_terminal(dispatch_id="plan-child-1", terminal_status="completed")
    found = plan_handoff_for_conductor(nest_under_dispatch_id=conductor_id)
    assert found is not None
    assert found["density_triage"] == "implement_ready"
