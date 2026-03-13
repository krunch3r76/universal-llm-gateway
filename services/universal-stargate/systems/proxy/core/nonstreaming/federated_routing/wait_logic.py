"""
Eviction wait-loop and retry helpers for transient federated routing infeasibility.

The wait loop uses generation-aware state change waiting to avoid missed wakeups
while preserving deterministic timeout, cancellation, and observability semantics.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .events import _emit_event_safe

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.selection.decision.stability import StickyPlacementTracker

    from ..context import RequestContext

logger = get_logger(__name__)

# Eviction wait queue depth (for routing.eviction.wait.started payload and monitoring).
_eviction_wait_queue_depth: int = 0


async def _wait_and_retry_selection(
    *,
    federated_manager: "FederatedGatewayManager",
    decision_engine: Any,
    placement: Any,
    context: "RequestContext",
    event_bus: "EventBus | None",
    timeout_s: float,
    stability_tracker: "StickyPlacementTracker",
) -> tuple[Any, Any, int]:
    """
    Wait for federated state changes, then retry selection until success or timeout.
    """
    from src.scheduling.events.routing import (
        RoutingEvictionWaitCancelled,
        RoutingEvictionWaitResolved,
        RoutingEvictionWaitStarted,
        RoutingEvictionWaitTimeout,
    )
    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )

    global _eviction_wait_queue_depth
    wait_start = time.monotonic()
    max_iterations = 60
    trace: Any = None

    _eviction_wait_queue_depth += 1

    if event_bus:
        await _emit_event_safe(
            event_bus,
            RoutingEvictionWaitStarted(
                request_id=context.request_id,
                model_id=str(placement.model_id),
                timeout_s=timeout_s,
                queue_depth=_eviction_wait_queue_depth,
            ),
            "routing.eviction.wait.started",
        )

    try:
        for _ in range(max_iterations):
            elapsed_s = time.monotonic() - wait_start
            if elapsed_s >= timeout_s:
                break

            state_version = federated_manager.get_state_version()

            fresh_gateways = federated_manager.get_all_gateways()
            gateways_for_retry = [
                g
                for g in federated_gateways_to_routing_candidates(fresh_gateways)
                if g.name not in (context.excluded_gateway_ids or set())
            ]

            selected, trace = decision_engine.select(
                gateways=gateways_for_retry,
                placement=placement,
                request_id=context.request_id,
                sticky=context.model_sticky,
                stability_tracker=stability_tracker,
            )
            if selected is not None:
                waited_ms = int((time.monotonic() - wait_start) * 1000)
                if event_bus:
                    await _emit_event_safe(
                        event_bus,
                        RoutingEvictionWaitResolved(
                            request_id=context.request_id,
                            model_id=str(placement.model_id),
                            gateway_id=selected.name,
                            waited_ms=waited_ms,
                        ),
                        "routing.eviction.wait.resolved",
                    )
                return selected, trace, waited_ms

            still_transient = any(
                any(
                    f.constraint == "eviction_blocked_by_busy_models"
                    for f in c.constraints_failed
                )
                for c in (trace.candidates if trace else [])
            )
            if not still_transient:
                break

            remaining = max(0.1, timeout_s - (time.monotonic() - wait_start))
            await federated_manager.wait_for_state_change(state_version, remaining)

    except asyncio.CancelledError:
        waited_ms = int((time.monotonic() - wait_start) * 1000)
        if event_bus:
            await _emit_event_safe(
                event_bus,
                RoutingEvictionWaitCancelled(
                    request_id=context.request_id,
                    model_id=str(placement.model_id),
                    waited_ms=waited_ms,
                ),
                "routing.eviction.wait.cancelled",
            )
        raise

    finally:
        _eviction_wait_queue_depth -= 1

    waited_ms = int((time.monotonic() - wait_start) * 1000)
    if event_bus:
        await _emit_event_safe(
            event_bus,
            RoutingEvictionWaitTimeout(
                request_id=context.request_id,
                model_id=str(placement.model_id),
                waited_ms=waited_ms,
            ),
            "routing.eviction.wait.timeout",
        )
    return None, trace, waited_ms
