"""Tests for cursor-sdk sdk_mode resolution and plan closeout gate."""

from __future__ import annotations

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_mode import (
    apply_plan_mode_closeout_gate,
    enforce_plan_read_only,
    resolve_sdk_mode,
    validate_sdk_mode_at_admit,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


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
