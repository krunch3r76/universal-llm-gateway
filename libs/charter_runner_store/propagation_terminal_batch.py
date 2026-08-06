"""Batch settle/reconcile helpers for open propagation ledger rows.

Split from ``propagation_terminal`` so single-row settlement stays under the
SLOC ceiling while drain-supervisor and sweep callers share one import surface.
"""

from __future__ import annotations

from typing import Any

from .propagation_ledger import list_open_rows
from .propagation_terminal import ProbeFn, SettleResult, settle_open_row


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
    "default_probe",
    "reconcile_all_open_rows",
    "settle_open_rows_for_service",
]
