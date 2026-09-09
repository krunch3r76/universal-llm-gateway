"""Resolve cursor-sdk ``AgentOptions.mode`` (agent vs plan) at admit and closeout."""

from __future__ import annotations

import json
import re
from typing import Literal

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_sdk_packet import extract_sdk_mode_from_packet
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

SdkMode = Literal["agent", "plan"]

_IMPLEMENT_CLASS_CONTRACTS = frozenset({"implement", "pure-mechanical", "conductor"})
_PLAN_CLOSEOUT_VERDICT = "plan:closeout_verdict"
_IMPLEMENT_READY_RE = re.compile(
    r"^density(?:_triage)?:\s*implement[_-]?ready\b",
    re.IGNORECASE | re.MULTILINE,
)


def body_implement_ready(body: str | None) -> bool:
    """True when directive/packet body stamps implement-ready density."""
    return bool(_IMPLEMENT_READY_RE.search(body or ""))


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


def nested_auto_sdk_mode(
    *,
    contract: str,
    body: str,
    implement_ready: bool | None = None,
) -> SdkMode | None:
    """cursor-auto nested POST: plan-first for sparse recon legs only."""
    if contract not in {"ask", "recon", "seed"}:
        return None
    if implement_ready if implement_ready is not None else body_implement_ready(body):
        return None
    return "plan"


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


def apply_plan_mode_closeout_gate(
    *,
    sdk_mode: SdkMode | None,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    landed: bool | None,
    deviations: list[str] | None,
    artifact_paths: list[str],
    offgit_deliverable_uris: list[str] | None,
) -> tuple[CloseoutStatus, WorkOutcome | None, bool | None, list[str], str | None]:
    """Plan dispatches never land; verdict is PLAN_COMPLETE or PARTIAL only."""
    if sdk_mode != "plan":
        return status, work_outcome, landed, list(deviations or []), None

    out_devs = list(deviations or [])
    if landed:
        if "plan:land_claim_forbidden" not in out_devs:
            out_devs.append("plan:land_claim_forbidden")

    has_artifacts = bool(artifact_paths or offgit_deliverable_uris)
    if status == CloseoutStatus.COMPLETE:
        status = CloseoutStatus.PARTIAL
    if work_outcome == WorkOutcome.SHIPPED:
        work_outcome = WorkOutcome.UNVERIFIED

    verdict = "PLAN_COMPLETE" if has_artifacts else "PARTIAL"
    if f"{_PLAN_CLOSEOUT_VERDICT}={verdict}" not in out_devs:
        out_devs.append(f"{_PLAN_CLOSEOUT_VERDICT}={verdict}")

    return status, work_outcome, None, out_devs, verdict


__all__ = [
    "SdkMode",
    "apply_plan_mode_closeout_gate",
    "body_implement_ready",
    "enforce_plan_read_only",
    "explicit_sdk_mode",
    "nested_auto_sdk_mode",
    "resolve_sdk_mode",
    "sdk_mode_for_dispatch",
    "sdk_mode_from_record_json",
    "validate_sdk_mode_at_admit",
]
