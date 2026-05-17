"""
Terminal rejection-side event emission helpers.

Emits overflow and routing failure events only after the request has
definitively failed selection recovery. Extracted during modularization.
"""

import time
from typing import TYPE_CHECKING

from ....events import _emit_overflow_failed_event

if TYPE_CHECKING:
    from systems.routing.selection.types import SelectionTrace

    from ....context import RequestContext


async def _emit_terminal_overflow_failure_if_needed(
    *,
    event_bus,
    context: "RequestContext",
) -> None:
    """Emit overflow failure only if the request later dies in terminal rejection."""
    if event_bus is None:
        return

    tried_gateways = context._overflow_failed_tried_gateways
    reason = context._overflow_failed_reason
    if not tried_gateways or reason is None:
        return

    await _emit_overflow_failed_event(
        event_bus=event_bus,
        request_id=context.request_id,
        model_id=context.selected_model,
        tried_gateways=tried_gateways,
        reason=reason,
    )


async def _emit_terminal_routing_failure(
    *,
    event_bus,
    context: "RequestContext",
    trace: "SelectionTrace | None",
    reason: str,
) -> None:
    """Emit scheduler.routing.failed only for terminal rejection outcomes."""
    if event_bus is None:
        return

    from src.scheduling.events import RoutingDecisionFailed

    candidate_count = len(trace.candidates) if trace else 0
    evaluation_time_ms = trace.evaluation_time_ms if trace else 0.0
    original_model_id = trace.original_model_id if trace else None
    timestamp = time.time()

    await event_bus.publish_nowait(
        RoutingDecisionFailed(
            model_id=context.selected_model.routing_key,
            candidate_count=candidate_count,
            evaluation_time_ms=evaluation_time_ms,
            timestamp=timestamp,
            reason=reason,
            original_model_id=original_model_id,
            request_id=context.request_id,
        )
    )


async def _emit_terminal_failure_events(
    *,
    event_bus,
    context: "RequestContext",
    trace: "SelectionTrace | None",
    reason: str,
) -> None:
    """Emit both terminal overflow and routing failure events for a final rejection.

    Deduplicating combinator so each terminal branch need not call the pair.
    """
    await _emit_terminal_overflow_failure_if_needed(
        event_bus=event_bus, context=context
    )
    await _emit_terminal_routing_failure(
        event_bus=event_bus, context=context, trace=trace, reason=reason
    )
