"""Event-bus trace emission helpers for routing decision observability.

The decision engine delegates event construction and asynchronous publish
behavior here to keep selection logic focused and to standardize failure
handling for fire-and-forget telemetry delivery.
"""

from __future__ import annotations

import asyncio
from typing import Any

from universal_logging import get_logger

from ..types import DecisionTrace

logger = get_logger(__name__)


def emit_decision_trace(
    *,
    event_bus: Any,
    trace: DecisionTrace,
    include_candidate_details: bool,
) -> None:
    """Publish a routing decision event without blocking request selection.

    The caller provides an event bus and a fully built trace. This helper maps
    success and failure outcomes to event types, then schedules asynchronous
    publish work on the current event loop when available.
    """
    if not event_bus:
        return

    try:
        from src.scheduling.events import RoutingDecision

        event_data = trace.to_event_payload(
            include_candidates=include_candidate_details,
        )

        # Emit every selection attempt as scheduler.routing.decided, even when
        # no gateway is currently feasible. Terminal failure is emitted later by
        # the rejection path once all retryable waits are exhausted.
        event = RoutingDecision(
            model_id=event_data["model_id"],
            selection_reason=event_data["selection_reason"],
            candidate_count=event_data["candidate_count"],
            feasible_count=event_data["feasible_count"],
            evaluation_time_ms=event_data["evaluation_time_ms"],
            timestamp=event_data["timestamp"],
            original_model_id=event_data.get("original_model_id"),
            selected_gateway=event_data.get("selected_gateway"),
            selection_tier=event_data.get("selection_tier"),
            request_id=event_data.get("request_id"),
            candidates=event_data.get("candidates"),
        )

        try:
            asyncio.get_running_loop()
            task = asyncio.create_task(event_bus.publish_nowait(event))

            def _on_done(done_task: asyncio.Task) -> None:
                try:
                    exc = done_task.exception()
                except Exception as callback_exc:
                    logger.error(
                        "Event bus callback failed for %s: %s",
                        trace.model_id,
                        callback_exc,
                        exc_info=True,
                    )
                    return
                if exc:
                    logger.error("Event bus publish failed: %s", exc, exc_info=True)

            task.add_done_callback(_on_done)
        except RuntimeError as exc:
            logger.warning(
                "Failed to emit decision trace for %s: no running event loop (%s)",
                trace.model_id,
                exc,
            )
    except Exception as exc:
        logger.error(
            "Failed to emit decision trace for %s: %s",
            trace.model_id,
            exc,
            exc_info=True,
        )
