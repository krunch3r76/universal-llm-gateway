"""Reconcile structured closeout status with executor §2 authored status.

Machine ``work_outcome`` / structured ``status`` are independent of §2
(``resolve_work_outcome`` / ``project_status_from_work_outcome``). When §2
declares an incomplete commission — or, on the no-deliverables-expected path,
authors no status at all — and the machine grade is optimistic
(shipped/complete), primary structured fields retain the machine grade;
disagreement is recorded under ``status_authority_disagreement`` without
making the claim authoritative over measurement (arc 7070).
"""

from __future__ import annotations

from typing import Any

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.lane_a_status import (
    extract_status_claim,
)

# Incomplete §2 tokens that must not be overridden by machine shipped/complete.
_AUTHORED_INCOMPLETE = frozenset({"partial", "blocked"})

_AUTHORITY_CITE = (
    "machine_measurement"
    " — capture/verification grade; "
    "claim surface: status_claim@§2; "
    "machine grade: cursor_sdk_capture_status.resolve_work_outcome "
    "(artifact/verification only)"
)


def _machine_optimistic(
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
) -> bool:
    return status == CloseoutStatus.COMPLETE or work_outcome == WorkOutcome.SHIPPED


def _record_disagreement(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    authored_label: str,
    deviations: list[str],
) -> tuple[CloseoutStatus, WorkOutcome | None, dict[str, Any], list[str]]:
    """Preserve machine grade; record claim divergence without capping primary."""
    machine_status = status.value
    machine_wo = work_outcome.value if work_outcome is not None else None
    token = (
        f"status_disagreement:authored_{authored_label}_vs_machine_{machine_status}"
    )
    if token not in deviations:
        deviations.append(token)
    disagreement: dict[str, Any] = {
        "authoritative": "machine_measurement",
        "authority_cite": _AUTHORITY_CITE,
        "authored_status": None if authored_label == "absent" else authored_label,
        "machine_status": machine_status,
        "machine_work_outcome": machine_wo,
        "primary_status": machine_status,
        "primary_work_outcome": machine_wo,
    }
    return status, work_outcome, disagreement, deviations


def reconcile_structured_with_authored(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    sidecar_markdown: str | None,
    deviations: list[str] | None = None,
    deliverables_expected: bool = False,
) -> tuple[CloseoutStatus, WorkOutcome | None, dict[str, Any] | None, list[str]]:
    """Record claim vs measurement divergence without capping machine grade.

    Returns ``(status, work_outcome, disagreement|None, deviations)``.

    When authored ∈ {partial, blocked} and machine is optimistic, or when
    authored is absent with ``deliverables_expected=False``, records disagreement
    but leaves primary status/work_outcome at the machine grade.

    No-op when authored status is ``complete``, or when machine grade is
    already non-optimistic.
    """
    out_devs = list(deviations or [])
    if not _machine_optimistic(status, work_outcome):
        return status, work_outcome, None, out_devs

    authored: str | None
    if not sidecar_markdown or not sidecar_markdown.strip():
        authored = None
    else:
        authored = extract_status_claim(sidecar_markdown)

    if authored in _AUTHORED_INCOMPLETE:
        return _record_disagreement(
            status=status,
            work_outcome=work_outcome,
            authored_label=authored,
            deviations=out_devs,
        )

    if authored is None and not deliverables_expected:
        return _record_disagreement(
            status=status,
            work_outcome=work_outcome,
            authored_label="absent",
            deviations=out_devs,
        )

    return status, work_outcome, None, out_devs


def refresh_disagreement_after_machine_gate(
    *,
    disagreement: dict[str, Any],
    post_gate_status: CloseoutStatus,
    post_gate_work_outcome: WorkOutcome | None,
    pre_gate_status: CloseoutStatus,
    status_incomplete_class: str | None = None,
) -> dict[str, Any]:
    """Refresh stale ``machine_status`` after a later machine gate changed primary.

    Reconcile records ``machine_status`` at reconcile time; lane-B (and similar
    gates) may downgrade primary afterward. B2 prefers ``machine_status`` — without
    refresh the disagreement block carries a pre-gate photograph (arc 6655 R3).
    """
    if post_gate_status == pre_gate_status:
        return disagreement
    refreshed: dict[str, Any] = {
        **disagreement,
        "machine_status": post_gate_status.value,
        "primary_status": post_gate_status.value,
    }
    if status_incomplete_class is not None:
        wo = (
            post_gate_work_outcome.value
            if post_gate_work_outcome is not None
            else None
        )
        refreshed["machine_work_outcome"] = wo
        refreshed["primary_work_outcome"] = wo
    return refreshed
