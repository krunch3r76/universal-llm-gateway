"""Reconcile structured closeout status with executor §2 authored status.

Machine ``work_outcome`` / structured ``status`` are independent of §2
(``resolve_work_outcome`` / ``project_status_from_work_outcome``). When §2
declares an incomplete commission and the machine grade is optimistic
(shipped/complete), primary structured fields must not silently claim
completion — §2 is authoritative for commission judgment; the machine grade
is preserved under ``status_authority_disagreement``.
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


def reconcile_structured_with_authored(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    sidecar_markdown: str | None,
    deviations: list[str] | None = None,
) -> tuple[CloseoutStatus, WorkOutcome | None, dict[str, Any] | None, list[str]]:
    """Cap optimistic machine grade when §2 authored status is incomplete.

    Returns ``(status, work_outcome, disagreement|None, deviations)``.
    No-op when §2 status is absent or not incomplete, or when machine grade
    is already non-optimistic.
    """
    out_devs = list(deviations or [])
    if not sidecar_markdown or not sidecar_markdown.strip():
        return status, work_outcome, None, out_devs

    authored = extract_status(sidecar_markdown)
    if authored is None or authored not in _AUTHORED_INCOMPLETE:
        return status, work_outcome, None, out_devs

    machine_optimistic = status == CloseoutStatus.COMPLETE or (
        work_outcome == WorkOutcome.SHIPPED
    )
    if not machine_optimistic:
        return status, work_outcome, None, out_devs

    machine_status = status.value
    machine_wo = work_outcome.value if work_outcome is not None else None
    capped_status = CloseoutStatus.PARTIAL
    capped_wo = WorkOutcome.NOT_SHIPPED
    token = f"status_disagreement:authored_{authored}_vs_machine_{machine_status}"
    if token not in out_devs:
        out_devs.append(token)

    disagreement: dict[str, Any] = {
        "authoritative": "authored_section2",
        "authority_cite": _AUTHORITY_CITE,
        "authored_status": authored,
        "machine_status": machine_status,
        "machine_work_outcome": machine_wo,
        "primary_status": capped_status.value,
        "primary_work_outcome": capped_wo.value,
    }
    return capped_status, capped_wo, disagreement, out_devs
