"""Terminal propagation ledger rows from observed liveness — not restart status."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from deploy_identity.code_ref_relation import (
    code_ref_relation_from_observed,
    code_ref_satisfied,
)
from implement_admission.propagation_row import PropagationRow, default_proof
from universal_logging import get_logger

from .propagation_determination import classify_probe, outgoing_generation_ruled_out
from .propagation_ledger import (
    OpenPropagationProjection,
    close_row,
    fail_row,
    list_open_rows,
    set_defer_reason,
)

logger = get_logger(__name__)

ProbeFn = Callable[[str], dict[str, Any] | None]
Outcome = Literal["closed", "failed", "deferred", "unsettled", "skipped"]

_UNCHECKABLE_HEAD = "code_ref is literal HEAD — permanently uncheckable"
_OUTGOING_DEFER = "proof_pending_outgoing_generation"
_UNATTRIBUTED_CONTRADICTION = "proof_contradicted_generation_unverified"
_INDETERMINATE_PROBE = "proof_indeterminate_probe_unreadable"


def _probe_is_outgoing_generation(
    payload: dict[str, Any],
    *,
    settle_not_before_monotonic: float,
) -> bool:
    """True when uptime_s implies the probed process predates restart completion."""
    uptime = payload.get("uptime_s")
    if not isinstance(uptime, (int, float)):
        return False
    process_started_monotonic = time.monotonic() - float(uptime)
    return process_started_monotonic < settle_not_before_monotonic


@dataclass(frozen=True)
class SettleResult:
    """One row settlement attempt."""

    row_id: str
    service: str
    code_ref: str
    outcome: Outcome
    detail: str


def _is_literal_head(code_ref: str) -> bool:
    return str(code_ref or "").strip().upper() == "HEAD"


def _projection_to_row(row: OpenPropagationProjection) -> PropagationRow:
    return PropagationRow(
        service=row.service,
        code_ref=row.code_ref,
        safe_window=row.safe_window,
        proof=default_proof(row.service),
        proof_class=row.proof_class,
    )


def _probe_for_projection(row: OpenPropagationProjection) -> dict[str, Any] | None:
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        dispatch_for_projection,
    )

    result = dispatch_for_projection(row)
    if result.error is not None:
        return None
    return result.payload


def _proof_matches_projection(
    row: OpenPropagationProjection,
    payload: dict[str, Any] | None,
    *,
    settle_not_before_monotonic: float | None = None,
) -> bool:
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        proof_observed,
    )

    return proof_observed(
        _projection_to_row(row),
        payload,
        settle_not_before_monotonic=settle_not_before_monotonic,
    )


def proof_matches_row(row: OpenPropagationProjection, payload: dict[str, Any] | None) -> bool:
    """True when observed code_version satisfies the row's code_ref via ancestry."""
    if payload is None:
        return False
    observed = payload.get("code_version")
    return isinstance(observed, str) and code_ref_satisfied(row.code_ref, observed)


def _fresh_projection(row: OpenPropagationProjection) -> OpenPropagationProjection:
    for item in list_open_rows():
        if item.row_id == row.row_id:
            return item
    return row


def settle_open_row(
    row: OpenPropagationProjection,
    probe: ProbeFn,
    *,
    defer_if_unreachable: bool = False,
    settle_not_before_monotonic: float | None = None,
) -> SettleResult:
    """Close or fail one open row from a client-reachable liveness probe."""
    from services.git_integration_worker.cursor_auto.propagation_proof_reconcile import (
        reconcile_unsupported_proof_class,
    )

    row = _fresh_projection(row)
    unsupported = reconcile_unsupported_proof_class(row)
    if unsupported is not None:
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="failed",
            detail=unsupported,
        )
    row = _fresh_projection(row)
    if _is_literal_head(row.code_ref):
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="unsettled",
            detail=_UNCHECKABLE_HEAD,
        )

    payload = (
        _probe_for_projection(row)
        if probe is default_probe
        else probe(row.service)
    )
    if payload is None:
        if defer_if_unreachable:
            set_defer_reason(row.row_id, "proof_pending_after_drain")
            return SettleResult(
                row_id=row.row_id,
                service=row.service,
                code_ref=row.code_ref,
                outcome="deferred",
                detail="probe unreachable — row left open",
            )
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="unsettled",
            detail="probe unreachable",
        )

    if (
        settle_not_before_monotonic is not None
        and _probe_is_outgoing_generation(
            payload, settle_not_before_monotonic=settle_not_before_monotonic
        )
    ):
        if defer_if_unreachable:
            set_defer_reason(row.row_id, _OUTGOING_DEFER)
            return SettleResult(
                row_id=row.row_id,
                service=row.service,
                code_ref=row.code_ref,
                outcome="deferred",
                detail="probe from outgoing generation — row left open",
            )
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="unsettled",
            detail="probe from outgoing generation",
        )

    matches = (
        _proof_matches_projection(
            row,
            payload,
            settle_not_before_monotonic=settle_not_before_monotonic,
        )
        if probe is default_probe
        else proof_matches_row(row, payload)
    )
    determination = classify_probe(payload, code_ref=row.code_ref, matched=matches)
    if determination == "matched":
        close_row(row.row_id, proof_payload=payload)
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="closed",
            detail=f"proof matched code_ref={row.code_ref}",
        )

    observed = payload.get("code_version")
    relation = (
        payload.get("code_ref_relation")
        if isinstance(payload.get("code_ref_relation"), str)
        else code_ref_relation_from_observed(row.code_ref, observed)
    )
    ruled_out = outgoing_generation_ruled_out(
        payload,
        settle_not_before_monotonic=settle_not_before_monotonic,
        now_monotonic=time.monotonic(),
    )
    if determination == "contradicted" and ruled_out:
        fail_payload = {
            **payload,
            "expected_code_ref": row.code_ref,
            "observed_code_version": observed,
            "code_ref_relation": relation,
        }
        fail_row(
            row.row_id,
            proof_payload=fail_payload,
            reason="code_version_mismatch",
        )
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="failed",
            detail=f"mismatch: expected {row.code_ref} observed {observed!r}",
        )

    # Either the probe did not answer (unreadable or half-unreachable payload),
    # or it contradicted the target but cannot be attributed to the incoming
    # generation. Failing here is terminal and unrecoverable, so the row stays
    # open and correctable instead.
    if determination == "contradicted":
        reason = _UNATTRIBUTED_CONTRADICTION
        detail = (
            f"contradiction not attributable to the incoming generation "
            f"(expected {row.code_ref} observed {observed!r}) — row left open"
        )
    else:
        reason = _INDETERMINATE_PROBE
        detail = "probe carried no readable code_version — row left open"
    if defer_if_unreachable:
        set_defer_reason(row.row_id, reason)
    return SettleResult(
        row_id=row.row_id,
        service=row.service,
        code_ref=row.code_ref,
        outcome="deferred" if defer_if_unreachable else "unsettled",
        detail=detail,
    )


def settle_open_rows_for_service(
    service: str,
    probe: ProbeFn,
    *,
    defer_if_unreachable: bool = True,
    settle_not_before_monotonic: float | None = None,
) -> list[SettleResult]:
    """Settle all open rows for one service — drain-supervisor post-completion hook."""
    results: list[SettleResult] = []
    for row in list_open_rows():
        if row.service != service:
            continue
        results.append(
            settle_open_row(
                row,
                probe,
                defer_if_unreachable=defer_if_unreachable,
                settle_not_before_monotonic=settle_not_before_monotonic,
            )
        )
    return results


def reconcile_all_open_rows(
    probe: ProbeFn,
    *,
    settle_not_before_monotonic: float | None = None,
) -> dict[str, Any]:
    """One-pass reconcile: close matching rows; fail mismatches; report unsettled.

    A sweep with no known restart boundary cannot attribute a contradiction to
    the incoming generation, so mismatches report ``unsettled`` and the rows
    stay open. Pass ``settle_not_before_monotonic`` when reconciling after a
    known restart to re-enable terminal failure.
    """
    before = len(list_open_rows())
    results = [
        settle_open_row(
            row,
            probe,
            defer_if_unreachable=False,
            settle_not_before_monotonic=settle_not_before_monotonic,
        )
        for row in list_open_rows()
    ]
    after = len(list_open_rows())
    return {
        "before_open": before,
        "after_open": after,
        "closed": sum(1 for item in results if item.outcome == "closed"),
        "failed": sum(1 for item in results if item.outcome == "failed"),
        "unsettled": sum(1 for item in results if item.outcome == "unsettled"),
        "results": results,
    }


def default_probe(service: str) -> dict[str, Any] | None:
    """Import-safe default probe for GIW and MCP liveness endpoints."""
    from scripts.model_manager.ui.controller.charter_runner.propagation_execute import (
        probe_process_live,
    )

    return probe_process_live(service)


__all__ = [
    "SettleResult",
    "_probe_is_outgoing_generation",
    "default_probe",
    "proof_matches_row",
    "_probe_for_projection",
    "_projection_to_row",
    "reconcile_all_open_rows",
    "settle_open_row",
    "settle_open_rows_for_service",
]
