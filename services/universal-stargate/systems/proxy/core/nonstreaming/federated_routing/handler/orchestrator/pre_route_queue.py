"""
Pre-routing queue helpers for retryable capacity constraints.

When the initial selection has only transient blockers, this module performs
event-driven waiting and bounded reselection before hard failure.
"""

import time
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)


async def wait_for_retryable_capacity(
    *,
    selected_gateway: "Gateway | None",
    trace: "SelectionTrace | None",
    event_bus,
    decision_engine: "DecisionEngine",
    gateways_for_routing: list["Gateway"],
    placement: "Placement",
    context: "RequestContext",
    stability_tracker: "StickyPlacementTracker",
) -> tuple["Gateway | None", "SelectionTrace | None"]:
    """
    Queue and retry selection if trace indicates a retryable capacity condition.

    This logic preserves FIFO-ish behavior under contention while keeping
    admission and routing decisions in the same request lifecycle.
    """
    if selected_gateway is not None or event_bus is None:
        return selected_gateway, trace

    from src.scheduling.events.routing import (
        RoutingDequeued,
        RoutingQueued,
        RoutingTimeout,
    )

    from ....routing_wait import (
        QUEUE_TIMEOUT_S,
        extract_retryable_constraint,
        wait_for_capacity_signal,
    )

    retryable_constraint = extract_retryable_constraint(trace)
    if not retryable_constraint:
        return selected_gateway, trace

    queue_start = time.monotonic()
    deadline = queue_start + QUEUE_TIMEOUT_S

    await event_bus.publish_async_nowait(
        RoutingQueued(
            request_id=context.request_id,
            model_id=str(context.selected_model),
            constraint=retryable_constraint,
            timestamp=time.time(),
        )
    )

    logger.info(
        "Pre-routing queue: %s waiting for capacity (constraint=%s, budget=%.1fs)",
        context.selected_model,
        retryable_constraint,
        QUEUE_TIMEOUT_S,
    )

    while not selected_gateway and time.monotonic() < deadline:
        try:
            signaled = await wait_for_capacity_signal(
                event_bus=event_bus,
                model_id=context.selected_model.routing_key,
                request_id=context.request_id,
                deadline=deadline,
            )
        except Exception as exc:
            logger.warning(
                "Pre-routing queue wait failed for %s: %s",
                context.selected_model,
                exc,
            )
            break

        selected_gateway, trace = decision_engine.select(
            gateways=gateways_for_routing,
            placement=placement,
            request_id=context.request_id,
            sticky=context.model_sticky,
            stability_tracker=stability_tracker,
        )
        if selected_gateway:
            wait_ms = (time.monotonic() - queue_start) * 1000.0
            await event_bus.publish_async_nowait(
                RoutingDequeued(
                    request_id=context.request_id,
                    model_id=str(context.selected_model),
                    gateway_id=selected_gateway.name,
                    wait_ms=wait_ms,
                    timestamp=time.time(),
                )
            )
            logger.info(
                "Pre-routing dequeued: %s -> %s after %.0fms",
                context.selected_model,
                selected_gateway.name,
                wait_ms,
            )
            return selected_gateway, trace

        if not signaled and time.monotonic() >= deadline:
            break

    wait_ms = (time.monotonic() - queue_start) * 1000.0
    await event_bus.publish_async_nowait(
        RoutingTimeout(
            request_id=context.request_id,
            model_id=str(context.selected_model),
            constraint=retryable_constraint,
            wait_ms=wait_ms,
            timestamp=time.time(),
        )
    )
    logger.warning(
        "Pre-routing timeout: %s after %.0fms (constraint=%s)",
        context.selected_model,
        wait_ms,
        retryable_constraint,
    )
    return selected_gateway, trace
