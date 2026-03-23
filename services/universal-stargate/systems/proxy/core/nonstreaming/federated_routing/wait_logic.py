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

# Default startup queue window: hold requests for up to this many seconds
# while Stargate waits for its first gateway to connect.
# Configurable via stargate.yaml: request_queue.startup_queue_timeout_s
DEFAULT_STARTUP_QUEUE_TIMEOUT_S: float = 180.0


async def wait_for_startup_gateway(
    *,
    federated_manager: "FederatedGatewayManager",
    context: "RequestContext",
    event_bus: "EventBus | None",
    timeout_s: float,
) -> bool:
    """
    Hold a request during Stargate startup until a gateway connects or timeout.

    Called when no healthy gateways exist AND Stargate is within its startup
    window (uptime < startup_queue_timeout_s). Uses generation-aware waiting
    so the wait wakes immediately when the first gateway registers.

    Returns True if at least one healthy gateway appeared, False on timeout.
    Propagates CancelledError so client disconnects abort cleanly.
    """
    from src.scheduling.events.routing import (
        RoutingStartupQueued,
        RoutingStartupResolved,
        RoutingStartupTimeout,
    )

    wait_start = time.monotonic()

    if event_bus:
        await _emit_event_safe(
            event_bus,
            RoutingStartupQueued(
                request_id=context.request_id,
                model_id=str(context.selected_model),
                uptime_s=federated_manager.uptime_s,
                timeout_s=timeout_s,
            ),
            "routing.startup.queued",
        )

    try:
        while True:
            elapsed_s = time.monotonic() - wait_start
            if elapsed_s >= timeout_s:
                break

            state_version = federated_manager.get_state_version()

            gateways = federated_manager.get_healthy_gateways()
            if gateways:
                waited_ms = int((time.monotonic() - wait_start) * 1000)
                first_gw = gateways[0].gateway_id if gateways else "unknown"
                if event_bus:
                    await _emit_event_safe(
                        event_bus,
                        RoutingStartupResolved(
                            request_id=context.request_id,
                            model_id=str(context.selected_model),
                            gateway_id=first_gw,
                            waited_ms=waited_ms,
                            uptime_s=federated_manager.uptime_s,
                        ),
                        "routing.startup.resolved",
                    )
                logger.info(
                    "Startup queue resolved for %s after %dms: gateway %s connected",
                    context.selected_model,
                    waited_ms,
                    first_gw,
                )
                return True

            remaining = max(0.1, timeout_s - (time.monotonic() - wait_start))
            await federated_manager.wait_for_state_change(state_version, remaining)

    except asyncio.CancelledError:
        raise

    waited_ms = int((time.monotonic() - wait_start) * 1000)
    if event_bus:
        await _emit_event_safe(
            event_bus,
            RoutingStartupTimeout(
                request_id=context.request_id,
                model_id=str(context.selected_model),
                waited_ms=waited_ms,
                uptime_s=federated_manager.uptime_s,
            ),
            "routing.startup.timeout",
        )
    logger.warning(
        "Startup queue timeout for %s after %dms: no gateway connected",
        context.selected_model,
        waited_ms,
    )
    return False


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
