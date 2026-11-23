"""
Helpers for eviction-wait terminal-state event emission.

Extracted from `wait_logic.py` to keep that module under the SLOC cap while
supporting the two distinct `routing.eviction.wait.timeout` exit paths —
`budget_exhausted` (waited full budget) and `non_transient` (first-iteration
bail when the retry trace no longer carries eviction_blocked_by_busy_models).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from universal_event_bus import EventBus


def build_exit_constraint_summary(trace: Any) -> list[dict]:
    """Per-candidate constraint snapshot for wait.timeout exit payload.

    Returns a list of ``{"gateway_id": str, "constraints_failed": list[str]}``
    — sorted constraint names per candidate — suitable for JSON payload. An
    empty list is returned when no trace or no candidates are available so the
    payload field is always present.
    """
    if not trace or not getattr(trace, "candidates", None):
        return []
    return [
        {
            "gateway_id": c.gateway.name,
            "constraints_failed": sorted({f.constraint for f in c.constraints_failed}),
        }
        for c in trace.candidates
    ]


async def emit_eviction_wait_budget_exhausted_before_entry(
    *,
    event_bus: "EventBus | None",
    request_id: str,
    model_id: str,
    trace: Any,
) -> None:
    """Emit routing.eviction.wait.timeout without entering the wait loop."""
    if event_bus is None:
        return
    from src.scheduling.events.routing import RoutingEvictionWaitTimeout

    from .events import _emit_event_safe

    await _emit_event_safe(
        event_bus,
        RoutingEvictionWaitTimeout(
            request_id=request_id,
            model_id=model_id,
            waited_ms=0,
            exit_reason="budget_exhausted_before_wait",
            exit_constraint_summary=build_exit_constraint_summary(trace),
        ),
        "routing.eviction.wait.timeout",
    )
