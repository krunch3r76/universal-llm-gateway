"""Resolve cursor-sdk ``AgentOptions.mode`` (agent vs plan) at admit and closeout."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_packet import (
    extract_sdk_mode_from_packet,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

SdkMode = Literal["agent", "plan"]

_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "pure-mechanical", "conductor"})
_PLAN_CLOSEOUT_VERDICT = "plan:closeout_verdict"


def explicit_sdk_mode(req: CursorDispatchRequest) -> SdkMode | None:
    """Return caller-supplied mode when ``sdk_mode`` was set on the wire body."""
    if "sdk_mode" in req.model_fields_set and req.sdk_mode is not None:
        return req.sdk_mode
    return None


def resolve_sdk_mode(
    req: CursorDispatchRequest,
    *,
    contract: str,
    packet_text: str,
    effective_read_only: bool,
) -> SdkMode:
    """Admission-time mode resolution (v1 rules)."""
    explicit = explicit_sdk_mode(req)
    if explicit is not None:
        return explicit
    packet_mode = extract_sdk_mode_from_packet(packet_text)
    if packet_mode is not None:
        return packet_mode
    contract_l = contract.lower()
    if contract_l in _IMPLEMENT_CLASS_CONTRACTS:
        return "agent"
    return "agent"


def validate_sdk_mode_at_admit(
    sdk_mode: SdkMode,
    *,
    contract: str,
) -> str | None:
    """Return a 422 detail string when *sdk_mode* is incompatible with *contract*."""
    if sdk_mode == "plan" and contract.lower() in _IMPLEMENT_CLASS_CONTRACTS:
        return (
            f"sdk_mode=plan is incompatible with contract={contract.lower()} "
            "(implement-class dispatches must use agent mode)"
        )
    return None


def enforce_plan_read_only(sdk_mode: SdkMode, effective_read_only: bool) -> bool:
    """``sdk_mode=plan`` always runs lease-exempt (read-only)."""
    if sdk_mode == "plan":
        return True
    return effective_read_only


def sdk_mode_from_record_json(record_json: str | None) -> SdkMode | None:
    """Read resolved ``sdk_mode`` stamped at admit."""
    if not record_json:
        return None
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("sdk_mode")
    if raw in ("agent", "plan"):
        return raw
    return None


def sdk_mode_for_dispatch(dispatch_id: str) -> SdkMode | None:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    with CursorDispatchLedger.instance()._connect() as conn:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return sdk_mode_from_record_json(str(row["record_json"] or ""))


@dataclass(frozen=True, slots=True)
class PlanCloseoutPredicate:
    """Inputs for R1 §5 PLAN_COMPLETE conjuncts."""

    has_artifacts: bool
    open_forks_key_present: bool
    open_forks: list[Any]
    spec_sha256: str | None
    dense_spec_valid: bool | None
    files_expected: list[str] | None
    acceptance_criteria: list[str] | None


def plan_closeout_verdict(inputs: PlanCloseoutPredicate) -> str:
    """Return PLAN_COMPLETE or PARTIAL per six §5 conjuncts."""
    if not inputs.open_forks_key_present:
        return "PARTIAL"
    complete = (
        inputs.has_artifacts
        and inputs.open_forks == []
        and bool(inputs.spec_sha256)
        and inputs.dense_spec_valid is True
        and bool(inputs.files_expected)
        and bool(inputs.acceptance_criteria)
    )
    return "PLAN_COMPLETE" if complete else "PARTIAL"


def apply_plan_mode_closeout_gate(
    *,
    sdk_mode: SdkMode | None,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    landed: bool | None,
    deviations: list[str] | None,
    artifact_paths: list[str],
    offgit_deliverable_uris: list[str] | None,
    open_forks: list[dict[str, Any]] | None = ...,
    spec_sha256: str | None = None,
    dense_spec_valid: bool | None = None,
    files_expected: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> tuple[CloseoutStatus, WorkOutcome | None, bool | None, list[str], str | None]:
    """Plan dispatches never land; verdict is PLAN_COMPLETE or PARTIAL only."""
    if sdk_mode != "plan":
        return status, work_outcome, landed, list(deviations or []), None

    out_devs = list(deviations or [])
    if landed:
        if "plan:land_claim_forbidden" not in out_devs:
            out_devs.append("plan:land_claim_forbidden")

    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    if work_outcome == WorkOutcome.SHIPPED:
        work_outcome = WorkOutcome.UNVERIFIED

    open_forks_key_present = open_forks is not ...
    forks_list = [] if open_forks is ... else list(open_forks or [])
    has_artifacts = bool(artifact_paths or offgit_deliverable_uris)
    verdict = plan_closeout_verdict(
        PlanCloseoutPredicate(
            has_artifacts=has_artifacts,
            open_forks_key_present=open_forks_key_present,
            open_forks=forks_list,
            spec_sha256=spec_sha256,
            dense_spec_valid=dense_spec_valid,
            files_expected=files_expected,
            acceptance_criteria=acceptance_criteria,
        )
    )
    if f"{_PLAN_CLOSEOUT_VERDICT}={verdict}" not in out_devs:
        out_devs.append(f"{_PLAN_CLOSEOUT_VERDICT}={verdict}")

    return status, work_outcome, None, out_devs, verdict


__all__ = [
    "PlanCloseoutPredicate",
    "SdkMode",
    "apply_plan_mode_closeout_gate",
    "enforce_plan_read_only",
    "explicit_sdk_mode",
    "plan_closeout_verdict",
    "resolve_sdk_mode",
    "sdk_mode_for_dispatch",
    "sdk_mode_from_record_json",
    "validate_sdk_mode_at_admit",
]
