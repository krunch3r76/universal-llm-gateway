"""Terminal propagation ledger rows from observed liveness — not restart status."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from deploy_identity.code_ref_relation import (
    code_ref_satisfied,
)
from implement_admission.propagation_row import PropagationRow, default_proof
from universal_logging import get_logger

from .propagation_determination import (
    classify_probe,
    outgoing_generation_ruled_out,
    proof_evaluable,
)
from .propagation_ledger import (
    OpenPropagationProjection,
    close_row,
    fail_row,
    list_open_rows,
    set_defer_reason,
    set_settle_boundary,
)
from .propagation_terminal_unsatisfiable import try_terminalize_unsatisfiable
from .propagation_version_satisfaction import classify_version_satisfaction

logger = get_logger(__name__)

ProbeFn = Callable[[str], dict[str, Any] | None]
Outcome = Literal["closed", "failed", "deferred", "unsettled", "skipped"]

_UNCHECKABLE_HEAD = "code_ref is literal HEAD — permanently uncheckable"
_OUTGOING_DEFER = "proof_pending_outgoing_generation"
_UNATTRIBUTED_CONTRADICTION = "proof_contradicted_generation_unverified"
_INDETERMINATE_PROBE = "proof_indeterminate_probe_unreadable"
_UNEVALUABLE_PAYLOAD = "proof_unevaluable_payload_shape"


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
    proof = row.proof or default_proof(row.service, row.proof_class)
    return PropagationRow(
        service=row.service,
        code_ref=row.code_ref,
        safe_window=row.safe_window,
        proof=proof,
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


def proof_matches_row(
    row: OpenPropagationProjection, payload: dict[str, Any] | None
) -> bool:
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


def _effective_settle_boundary(
    row: OpenPropagationProjection,
    settle_not_before_monotonic: float | None,
) -> float | None:
    """Caller boundary wins; else use the boundary persisted at defer time."""
    if settle_not_before_monotonic is not None:
        return settle_not_before_monotonic
    return row.settle_boundary_monotonic


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
    boundary = _effective_settle_boundary(row, settle_not_before_monotonic)
    if settle_not_before_monotonic is not None:
        set_settle_boundary(row.row_id, settle_not_before_monotonic)
        row = _fresh_projection(row)
        boundary = _effective_settle_boundary(row, settle_not_before_monotonic)
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
        _probe_for_projection(row) if probe is default_probe else probe(row.service)
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

    if boundary is not None and _probe_is_outgoing_generation(
        payload, settle_not_before_monotonic=boundary
    ):
        if defer_if_unreachable:
            set_defer_reason(row.row_id, _OUTGOING_DEFER)
            if boundary is not None:
                set_settle_boundary(row.row_id, boundary)
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

    if not proof_evaluable(payload, proof_class=row.proof_class):
        detail = (
            "proof predicate unevaluable on payload shape — "
            "declared proof class cannot run against this probe"
        )
        if defer_if_unreachable:
            set_defer_reason(row.row_id, _UNEVALUABLE_PAYLOAD)
            return SettleResult(
                row_id=row.row_id,
                service=row.service,
                code_ref=row.code_ref,
                outcome="deferred",
                detail=detail,
            )
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="unsettled",
            detail=detail,
        )

    from .propagation_terminal_retire import (
        observed_code_version_for_settle,
        try_close_on_version_satisfaction,
    )

    observed_str = observed_code_version_for_settle(row, payload)
    observed = observed_str
    satisfaction = classify_version_satisfaction(row.code_ref, observed_str)
    relation = satisfaction.relation

    exact_match = satisfaction.case == "exact_match"
    proof_passes = (
        _proof_matches_projection(
            row,
            payload,
            settle_not_before_monotonic=boundary,
        )
        if probe is default_probe
        else proof_matches_row(row, payload) and exact_match
    )
    determination = classify_probe(
        payload, code_ref=row.code_ref, matched=exact_match and proof_passes
    )

    # Settle has no harvest before/after today — retire identity arms see
    # before=None → indeterminate and close nothing via presence. Equal-ref
    # close requires proof_passes (or a future caller that supplies before).
    retired = try_close_on_version_satisfaction(
        row,
        payload,
        proof_passes=proof_passes,
        determination=determination,
        before=None,
    )
    if retired is not None:
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="closed",
            detail=retired,
        )

    if proof_passes and row.proof_class in ("served_artifact", "client_visible"):
        close_payload = {
            **payload,
            "code_ref_relation": relation,
            "version_satisfaction_case": satisfaction.case,
            "proof_predicate_satisfied": True,
        }
        close_row(row.row_id, proof_payload=close_payload)
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="closed",
            detail=(f"proof predicate satisfied for proof_class={row.proof_class}"),
        )

    ruled_out = outgoing_generation_ruled_out(
        payload,
        settle_not_before_monotonic=boundary,
        now_monotonic=time.monotonic(),
    )
    if (
        satisfaction.case == "stale_code"
        and determination == "contradicted"
        and ruled_out
    ):
        # Terminal event freeze of one settle attempt. True at write time; may
        # become false when the world catches up. Do not present status=failed
        # as current not-live — observe_code_ref_live cites a fresh probe.
        fail_payload = {
            **payload,
            "expected_code_ref": row.code_ref,
            "observed_code_version": observed,
            "code_ref_relation": relation,
            "version_satisfaction_case": satisfaction.case,
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
            detail=f"stale code: expected {row.code_ref} observed {observed!r}",
        )

    unsat = try_terminalize_unsatisfiable(
        row,
        payload=payload,
        observed=observed,
        satisfaction=satisfaction,
        determination=determination,
        defer_if_unreachable=defer_if_unreachable,
        settle_result_cls=SettleResult,
    )
    if unsat is not None:
        return unsat

    # Stale or contradicted reading without attribution — non-terminal.
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


# Batch helpers live in propagation_terminal_batch; re-export for callers.
from .propagation_terminal_batch import (  # noqa: E402
    default_probe,
    reconcile_all_open_rows,
    settle_open_rows_for_service,
)

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
