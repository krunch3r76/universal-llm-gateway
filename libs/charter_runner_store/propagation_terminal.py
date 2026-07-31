"""Terminal propagation ledger rows from observed liveness — not restart status."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from universal_logging import get_logger

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


def proof_matches_row(row: OpenPropagationProjection, payload: dict[str, Any] | None) -> bool:
    """True when observed code_version equals the row's code_ref."""
    if payload is None:
        return False
    observed = payload.get("code_version")
    return isinstance(observed, str) and observed == row.code_ref


def settle_open_row(
    row: OpenPropagationProjection,
    probe: ProbeFn,
    *,
    defer_if_unreachable: bool = False,
    settle_not_before_monotonic: float | None = None,
) -> SettleResult:
    """Close or fail one open row from a client-reachable liveness probe."""
    if _is_literal_head(row.code_ref):
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="unsettled",
            detail=_UNCHECKABLE_HEAD,
        )

    payload = probe(row.service)
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

    if proof_matches_row(row, payload):
        close_row(row.row_id, proof_payload=payload)
        return SettleResult(
            row_id=row.row_id,
            service=row.service,
            code_ref=row.code_ref,
            outcome="closed",
            detail=f"proof matched code_ref={row.code_ref}",
        )

    observed = payload.get("code_version")
    fail_payload = {
        **payload,
        "expected_code_ref": row.code_ref,
        "observed_code_version": observed,
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


def reconcile_all_open_rows(probe: ProbeFn) -> dict[str, Any]:
    """One-pass reconcile: close matching rows; fail mismatches; report unsettled."""
    before = len(list_open_rows())
    results = [
        settle_open_row(row, probe, defer_if_unreachable=False)
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
    "reconcile_all_open_rows",
    "settle_open_row",
    "settle_open_rows_for_service",
]
