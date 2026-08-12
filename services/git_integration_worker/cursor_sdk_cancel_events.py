"""Cancel / supersede observation events for cursor-sdk dispatches.

Kept separate from ``cursor_sdk_events`` (already >400 SLOC) so cancel lifecycle
stays a single-responsibility module. Publishes through the same registered
publisher via ``emit_frontier_event``.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_events import (
    _register_terminal_emitted,
    emit_frontier_event,
)

logger = get_logger(__name__)

# method ∈ run_cancel | bridge_abort | cancel_failed | not_live |
#           bridge_abort_escalate | queued_only | pre_register_live_run
_CANCEL_METHODS = frozenset(
    {
        "run_cancel",
        "bridge_abort",
        "cancel_failed",
        "not_live",
        "bridge_abort_escalate",
        "queued_only",
        "pre_register_live_run",
        "queue_withdraw",
    }
)


@event_factory
def FrontierSdkWorkerCancelled(  # noqa: N802
    dispatch_id: str,
    method: str,
    reason: str,
    thread_id: str | None = None,
    superseded_by: str | None = None,
    error: str | None = None,
    terminal_status: str = "cancelled",
) -> Event:
    """Emitted when a cursor-sdk dispatch is interrupted (supersede / cancel)."""
    payload: dict[str, Any] = {
        "dispatch_id": dispatch_id,
        "method": method,
        "reason": reason,
        "terminal_status": terminal_status,
    }
    if thread_id is not None:
        payload["thread_id"] = thread_id
    if superseded_by is not None:
        payload["superseded_by"] = superseded_by
    if error is not None:
        payload["error"] = error
    return Event(
        signal="frontier.sdk.worker.cancelled",
        payload=payload,
        scope="node",
    )


def emit_sdk_worker_cancelled(
    *,
    dispatch_id: str,
    method: str,
    reason: str,
    thread_id: str | None = None,
    superseded_by: str | None = None,
    error: str | None = None,
    terminal_status: str | None = None,
) -> None:
    """Publish cancel/supersede of a cursor-sdk dispatch to Event Service.

    ``method`` names the interrupt rung (``run_cancel``, ``bridge_abort``,
    ``pre_register_live_run``, …). Low-frequency lifecycle edge — safe as
    observation. ``pre_register_live_run`` must not default to
    ``terminal_status=cancelled`` — that vocabulary implies process stop.
    """
    cleaned = str(method or "").strip() or "not_live"
    if cleaned not in _CANCEL_METHODS:
        cleaned = "not_live"
    if terminal_status is None:
        status = (
            "displaced_pre_live"
            if cleaned == "pre_register_live_run"
            else "cancelled"
        )
    else:
        status = terminal_status
    event = FrontierSdkWorkerCancelled(
        dispatch_id=dispatch_id,
        method=cleaned,
        reason=reason,
        thread_id=thread_id,
        superseded_by=superseded_by,
        error=error,
        terminal_status=status,
    )
    emit_frontier_event(event)
    _register_terminal_emitted(dispatch_id)
    logger.warning(
        "cursor sdk worker cancelled: dispatch_id=%s thread_id=%s method=%s "
        "superseded_by=%s reason=%s error=%s",
        dispatch_id,
        thread_id,
        cleaned,
        superseded_by,
        reason,
        error,
    )


__all__ = [
    "FrontierSdkWorkerCancelled",
    "emit_sdk_worker_cancelled",
]
