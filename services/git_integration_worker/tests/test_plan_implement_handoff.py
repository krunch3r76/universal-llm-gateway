"""W3 plan-mode closeout → implement nest handoff bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from implement_admission.conductor_witness_table import row_witnesses
from implement_admission.conductor_witness_types import FoldDeps, Witness
from implement_admission.plan_implement_handoff import (
    build_nest_implement_hint,
    parse_nest_implement_hint,
    plan_implement_handoff_eligible,
)
from implement_admission.spec import CloseoutStatus, WorkOutcome
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.closeout_records import (
    SdkRunOutcome,
)
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_progress import (
    nest_implement_hint_in_closeout,
    plan_handoff_for_row,
    plan_implement_handoff_open_in_closeout,
)
from services.git_integration_worker.cursor_sdk_closeout.implement_body import (
    build_implement_closeout_body,
)
from services.git_integration_worker.cursor_sdk_mode import apply_plan_mode_closeout_gate
from services.git_integration_worker.cursor_sdk_plan_handoff_witness import (
    plan_handoff_for_conductor,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

pytestmark = pytest.mark.offline

_SOURCE_REF = "todo:plan-bridge-scratch"
_SLUG = "plan-bridge-scratch"
_CONDUCTOR_ID = "12345678-abcd-1234-abcd-123456789abc"


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs: object) -> dict[str, object]:
        return {
            "attributes": {"density_triage": "implement_ready"},
        }

    def list_relationships(
        self, entity_id: str, *, type_id: str | None = None
    ) -> list[dict[str, object]]:
        return []


class _StubPlanHandoff:
    def __init__(self, hint: dict[str, object] | None) -> None:
        self._hint = hint

    def plan_handoff_for_conductor(
        self, *, nest_under_dispatch_id: str
    ) -> dict[str, object] | None:
        return self._hint


def _hint() -> dict[str, object]:
    return {
        "verdict": "PLAN_COMPLETE",
        "density_triage": "implement_ready",
        "thread_id": "10363",
        "dispatch_id": "plan-child-1",
        "contract": "implement",
        "sdk_mode": "agent",
        "source_ref": _SOURCE_REF,
        "artifact_paths": ["cortex://notes/specs/plan-bridge.md"],
    }


def test_build_nest_implement_hint_requires_plan_complete_and_implement_ready() -> None:
    built = build_nest_implement_hint(
        plan_verdict="PLAN_COMPLETE",
        implement_ready=True,
        thread_id="10363",
        dispatch_id="d1",
        source_ref=_SOURCE_REF,
        artifact_paths=["cortex://notes/spec.md"],
    )
    assert built is not None
    assert built["verdict"] == "PLAN_COMPLETE"
    assert built["density_triage"] == "implement_ready"
    assert built["dispatch_id"] == "d1"

    assert (
        build_nest_implement_hint(
            plan_verdict="PARTIAL",
            implement_ready=True,
            thread_id="10363",
            dispatch_id="d1",
        )
        is None
    )
    assert (
        build_nest_implement_hint(
            plan_verdict="PLAN_COMPLETE",
            implement_ready=False,
            thread_id="10363",
            dispatch_id="d1",
        )
        is None
    )


def test_plan_closeout_body_includes_nest_implement_hint() -> None:
    packet = "---\ndensity_triage: implement_ready\n---\nplan body"
    outcome = SdkRunOutcome(
        body="status: partial",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
    )
    body = build_implement_closeout_body(
        dispatch_id="plan-d1",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref="workspaces://x/sidecar.md",
        result_bytes=100,
        thread_id="10363",
        work_item_ref=_SOURCE_REF,
        cortex_artifact_paths=["cortex://notes/specs/plan-bridge.md"],
        sdk_mode="plan",
        packet_text=packet,
    )
    payload = json.loads(body)
    assert payload.get("landed") is None
    assert "plan:land_claim_forbidden" not in (payload.get("deviations") or [])
    assert payload["status"] == "partial"
    hint = payload.get("nest_implement_hint")
    assert hint is not None
    assert hint["verdict"] == "PLAN_COMPLETE"
    assert hint["density_triage"] == "implement_ready"
    assert hint["thread_id"] == "10363"
    assert hint["contract"] == "implement"
    assert hint["sdk_mode"] == "agent"


def test_implement_closeout_omits_nest_implement_hint() -> None:
    outcome = SdkRunOutcome(
        body="status: complete",
        status="finished",
        duration_ms=100,
        tool_call_count=1,
    )
    body = build_implement_closeout_body(
        dispatch_id="impl-d1",
        outcome=outcome,
        degraded_reason=None,
        sidecar_ref="workspaces://x/sidecar.md",
        result_bytes=100,
        thread_id="10363",
        work_item_ref=_SOURCE_REF,
        sdk_mode="agent",
        packet_text="---\ndensity_triage: implement_ready\n---\n",
        landed=True,
        lane="B",
        commits_ahead=1,
    )
    payload = json.loads(body)
    assert "nest_implement_hint" not in payload
    assert payload.get("sdk_mode") is None


def test_apply_plan_mode_closeout_gate_still_forbids_land() -> None:
    status, work_outcome, landed, deviations, verdict = apply_plan_mode_closeout_gate(
        sdk_mode="plan",
        status=CloseoutStatus.COMPLETE,
        work_outcome=WorkOutcome.SHIPPED,
        landed=True,
        deviations=[],
        artifact_paths=["cortex://notes/spec.md"],
        offgit_deliverable_uris=None,
    )
    assert landed is None
    assert verdict == "PLAN_COMPLETE"
    assert "plan:land_claim_forbidden" in deviations


def test_witness_g3_via_plan_handoff_when_implement_ready(tmp_path: Path) -> None:
    tip_body = (
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        f"| G3 | Dense spec | OPEN | |\n\n"
        f"conductor dispatch_id `{_CONDUCTOR_ID}`\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        plan_handoff=_StubPlanHandoff(_hint()),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
    )
    g3 = witnesses.get("G3")
    assert g3 is not None
    assert g3.source == "closeout:nest_implement_hint"
    assert g3.detail == _CONDUCTOR_ID


def test_hop_progress_reads_nest_implement_hint_from_closeout_json() -> None:
    closeout = json.dumps({"nest_implement_hint": _hint()})
    assert plan_implement_handoff_open_in_closeout(closeout) is True
    parsed = nest_implement_hint_in_closeout(closeout)
    assert plan_implement_handoff_eligible(parsed)


def test_plan_handoff_for_row_reads_ledger_closeout_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="10363",
        model="cursor/composer-2.5",
        dispatch_id="plan-row-1",
        execution_id="exec-plan-row-1",
        message="plan",
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
        contract="recon",
        source_repo=str(tmp_path),
        lease_key=str(tmp_path),
    )
    closeout_json = json.dumps({"nest_implement_hint": _hint() | {"dispatch_id": "plan-row-1"}})
    ledger.merge_record_json(
        dispatch_id="plan-row-1",
        patch={"closeout_body": closeout_json, "sdk_mode": "plan"},
    )
    ledger.mark_terminal(dispatch_id="plan-row-1", terminal_status="completed")
    row = {"record_json": json.dumps({"closeout_body": closeout_json, "sdk_mode": "plan"})}
    hint = plan_handoff_for_row(row)
    assert hint is not None
    assert hint["verdict"] == "PLAN_COMPLETE"


def test_plan_handoff_for_conductor_reads_nested_plan_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    ledger = CursorDispatchLedger.instance()
    parent = CursorDispatchRequest(
        thread_id="10363",
        model="cursor/composer-2.5",
        dispatch_id=_CONDUCTOR_ID,
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
    closeout_json = json.dumps({"nest_implement_hint": _hint()})
    ledger.merge_record_json(
        dispatch_id="plan-child-1",
        patch={
            "nest_under": _CONDUCTOR_ID,
            "closeout_body": closeout_json,
            "sdk_mode": "plan",
        },
    )
    ledger.mark_terminal(dispatch_id="plan-child-1", terminal_status="completed")
    hint = plan_handoff_for_conductor(nest_under_dispatch_id=_CONDUCTOR_ID)
    assert hint is not None
    assert hint["density_triage"] == "implement_ready"
