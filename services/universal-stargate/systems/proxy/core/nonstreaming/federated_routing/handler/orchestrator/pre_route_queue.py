"""
Pre-routing queue helpers for retryable capacity constraints.

When the initial selection has only transient blockers, this module waits for
federated state changes (event-driven via FederatedGatewayManager) and retries
selection with fresh gateway snapshots on each iteration.
"""

import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)

_MAX_ITERATIONS = 60


async def wait_for_retryable_capacity(
    *,
    selected_gateway: "Gateway | None",
    trace: "SelectionTrace | None",
    event_bus: Any,
    decision_engine: "DecisionEngine",
    federated_manager: "FederatedGatewayManager | None",
    placement: "Placement",
    context: "RequestContext",
    stability_tracker: "StickyPlacementTracker",
) -> tuple["Gateway | None", "SelectionTrace | None"]:
    """
    Queue and retry selection if trace indicates a retryable capacity condition.

    Waits for federated state changes (model load/unload, resource updates) and
    re-fetches fresh gateway snapshots on each retry so selection reflects the
    current gateway state rather than the stale initial snapshot.
    """
    if selected_gateway is not None or federated_manager is None:
        return selected_gateway, trace

    from src.scheduling.events.routing import (
        RoutingDequeued,
        RoutingQueued,
        RoutingTimeout,
    )
    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )

    from ....routing_wait import (
        QUEUE_TIMEOUT_S,
        extract_retryable_constraint,
        register_demand,
        unregister_demand,
    )

    retryable_constraint = extract_retryable_constraint(trace)
    if not retryable_constraint:
        return selected_gateway, trace

    queue_start = time.monotonic()
    routing_key = context.selected_model.routing_key

    register_demand(routing_key, context.request_id)

    if event_bus:
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

    try:
        for _ in range(_MAX_ITERATIONS):
            elapsed = time.monotonic() - queue_start
            if elapsed >= QUEUE_TIMEOUT_S:
                break

            state_version = federated_manager.get_state_version()

            fresh_federated = federated_manager.get_all_gateways()
            fresh_gateways = [
                g
                for g in federated_gateways_to_routing_candidates(fresh_federated)
                if g.name not in (context.excluded_gateway_ids or set())
            ]

            selected_gateway, trace = decision_engine.select(
                gateways=fresh_gateways,
                placement=placement,
                request_id=context.request_id,
                sticky=context.model_sticky,
                stability_tracker=stability_tracker,
            )
            if selected_gateway:
                wait_ms = (time.monotonic() - queue_start) * 1000.0
                if event_bus:
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

            still_retryable = extract_retryable_constraint(trace) is not None
            if not still_retryable:
                break

            remaining = max(0.1, QUEUE_TIMEOUT_S - (time.monotonic() - queue_start))
            await federated_manager.wait_for_state_change(state_version, remaining)
    finally:
        unregister_demand(routing_key, context.request_id)

    wait_ms = (time.monotonic() - queue_start) * 1000.0
    if event_bus:
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
