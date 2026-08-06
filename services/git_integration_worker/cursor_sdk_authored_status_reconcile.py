"""Reconcile structured closeout status with executor §2 authored status.

Machine ``work_outcome`` / structured ``status`` are independent of §2
(``resolve_work_outcome`` / ``project_status_from_work_outcome``). When §2
declares an incomplete commission — or, on the no-deliverables-expected path,
authors no status at all — and the machine grade is optimistic
(shipped/complete), primary structured fields must not silently claim
completion. §2 is authoritative for commission judgment (absence = no claim
that authorizes completion when the machine grade was synthesized without
deliverable expectation); the machine grade is preserved under
``status_authority_disagreement``.
"""

from __future__ import annotations

from typing import Any

from implement_admission.spec import CloseoutStatus, WorkOutcome

from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_status,
)

# Incomplete §2 tokens that must not be overridden by machine shipped/complete.
_AUTHORED_INCOMPLETE = frozenset({"partial", "blocked"})

_AUTHORITY_CITE = (
    "authored_section2"
    " — commission-completion judgment; "
    "relay: closeout_relay_common.resolve_relay_status; "
    "discipline: dispatch-report-discipline rule 4; "
    "machine grade: cursor_sdk_capture_status.resolve_work_outcome "
    "(artifact/verification only)"
)


def _machine_optimistic(
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
) -> bool:
    return status == CloseoutStatus.COMPLETE or work_outcome == WorkOutcome.SHIPPED


def _cap_optimistic(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    authored_label: str,
    deviations: list[str],
) -> tuple[CloseoutStatus, WorkOutcome | None, dict[str, Any], list[str]]:
    """Cap primary fields; preserve machine grade under disagreement."""
    machine_status = status.value
    machine_wo = work_outcome.value if work_outcome is not None else None
    capped_status = CloseoutStatus.PARTIAL
    capped_wo = WorkOutcome.NOT_SHIPPED
    token = (
        f"status_disagreement:authored_{authored_label}_vs_machine_{machine_status}"
    )
    if token not in deviations:
        deviations.append(token)
    disagreement: dict[str, Any] = {
        "authoritative": "authored_section2",
        "authority_cite": _AUTHORITY_CITE,
        "authored_status": None if authored_label == "absent" else authored_label,
        "machine_status": machine_status,
        "machine_work_outcome": machine_wo,
        "primary_status": capped_status.value,
        "primary_work_outcome": capped_wo.value,
    }
    return capped_status, capped_wo, disagreement, deviations


def reconcile_structured_with_authored(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    sidecar_markdown: str | None,
    deviations: list[str] | None = None,
    deliverables_expected: bool = False,
) -> tuple[CloseoutStatus, WorkOutcome | None, dict[str, Any] | None, list[str]]:
    """Cap optimistic machine grade when §2 is incomplete or (narrowly) absent.

    Returns ``(status, work_outcome, disagreement|None, deviations)``.

    Always caps when authored ∈ {partial, blocked} and machine is optimistic
    (leg 1). Additionally caps when authored status is absent **and**
    ``deliverables_expected`` is False — the synthesis path that graded
    ``auto-40eacccc1b48`` shipped from transcript residue alone. Absent §2
    with ``deliverables_expected=True`` (evidence-backed implement) is a
    no-op for the absent rule; incomplete authored still caps.

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
        authored = extract_status(sidecar_markdown)

    if authored in _AUTHORED_INCOMPLETE:
        return _cap_optimistic(
            status=status,
            work_outcome=work_outcome,
            authored_label=authored,
            deviations=out_devs,
        )

    if authored is None and not deliverables_expected:
        return _cap_optimistic(
            status=status,
            work_outcome=work_outcome,
            authored_label="absent",
            deviations=out_devs,
        )

    return status, work_outcome, None, out_devs
