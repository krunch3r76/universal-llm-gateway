"""Confined ready-join before unprompted settle — bounded in-thread poll only."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from scripts.model_manager.ui.controller.restart_window_store import RETRY_AFTER_S

READY_JOIN_SERVICES = frozenset({"cdp_ask", "git_integration_worker"})
DEFAULT_READY_TIMEOUT_S = float(RETRY_AFTER_S)
DEFER_READY_WAIT = "proof_pending_ready_wait"
DEFER_READY_TIMEOUT = "proof_pending_ready_timeout"
DEFER_UNREACHABLE = "proof_pending_after_drain"

ReadyJoinOutcome = Literal["ready", "timeout", "skipped"]


@dataclass(frozen=True)
class ReadyJoinResult:
    """Outcome of a confined ready-join poll before unprompted settle."""

    outcome: ReadyJoinOutcome
    payload: dict[str, Any] | None
    defer_reason: str | None


def service_needs_ready_join(service: str) -> bool:
    """True when lifecycle-wrapper settle must wait for probe readiness."""
    return service in READY_JOIN_SERVICES


def _poll_budget_seconds(
    deadline_at: str | None,
    *,
    ready_timeout_s: float,
) -> float:
    budget = ready_timeout_s
    if not deadline_at:
        return budget
    try:
        deadline_dt = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
        remaining = (deadline_dt - datetime.now(UTC)).total_seconds()
        return min(budget, max(0.0, remaining))
    except ValueError:
        return budget


def ready_join_for_settle(
    service: str,
    *,
    deadline_at: str | None = None,
    ready_timeout_s: float = DEFAULT_READY_TIMEOUT_S,
    poll_interval_s: float = 0.5,
) -> ReadyJoinResult:
    """Poll ``PROCESS_LIVE_FETCHERS`` until ready or bounded timeout.

    Confined to the calling thread — no detached tasks, threads, or charter ticks.
    """
    if not service_needs_ready_join(service):
        return ReadyJoinResult(outcome="skipped", payload=None, defer_reason=None)

    from charter_runner_store.propagation_ledger import list_open_rows, set_defer_reason
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        probe_process_live,
    )

    end_mono = time.monotonic() + _poll_budget_seconds(
        deadline_at, ready_timeout_s=ready_timeout_s
    )
    marked_wait = False
    while time.monotonic() < end_mono:
        payload = probe_process_live(service)
        if payload is not None:
            return ReadyJoinResult(outcome="ready", payload=payload, defer_reason=None)
        if not marked_wait:
            for row in list_open_rows():
                if row.service == service:
                    set_defer_reason(row.row_id, DEFER_READY_WAIT)
            marked_wait = True
        remaining = end_mono - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(poll_interval_s, remaining))
    return ReadyJoinResult(
        outcome="timeout",
        payload=None,
        defer_reason=DEFER_READY_TIMEOUT,
    )


__all__ = [
    "DEFER_READY_TIMEOUT",
    "DEFER_READY_WAIT",
    "DEFER_UNREACHABLE",
    "DEFAULT_READY_TIMEOUT_S",
    "READY_JOIN_SERVICES",
    "ReadyJoinOutcome",
    "ReadyJoinResult",
    "ready_join_for_settle",
    "service_needs_ready_join",
]
