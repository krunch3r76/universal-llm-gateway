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
from .wait_emit import build_exit_constraint_summary

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision.protocols import RoutingKeyTracker
    from systems.routing.selection.decision.stability import StickyPlacementTracker

    from ..context import RequestContext

logger = get_logger(__name__)

# Starvation-triggered admission drain defaults. A waiter whose only blocker
# is eviction_blocked_by_busy_models for longer than this threshold causes the
# wait loop to pause admission on the continuously-busy models — the missing
# preemption primitive in the scheduler. Tunable via routing.*.
DEFAULT_STARVATION_DRAIN_THRESHOLD_S: float = 15.0
DEFAULT_DRAIN_DURATION_S: float = 30.0

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


DEFAULT_MODEL_GATEWAY_GRACE_TIMEOUT_S: float = 90.0


async def wait_for_model_gateway(
    *,
    federated_manager: "FederatedGatewayManager",
    context: "RequestContext",
    event_bus: "EventBus | None",
    model_id: str,
    timeout_s: float,
    unhealthy_gateway_ids: list[str],
) -> bool:
    """
    Wait for a specific model to reappear on a healthy gateway.

    The wait is model-scoped: only requests targeting this model pause while
    unrelated traffic continues. Returns True on recovery and False on timeout.
    """
    from src.scheduling.events.routing import (
        RoutingModelGraceQueued,
        RoutingModelGraceResolved,
        RoutingModelGraceTimeout,
    )

    wait_start = time.monotonic()
    sel = context.selected_model
    if event_bus:
        await _emit_event_safe(
            event_bus,
            RoutingModelGraceQueued(
                request_id=context.request_id,
                model_id=model_id,
                timeout_s=timeout_s,
                unhealthy_gateway_ids=unhealthy_gateway_ids,
            ),
            "routing.model.grace.queued",
        )
    try:
        while True:
            if time.monotonic() - wait_start >= timeout_s:
                break
            state_version = federated_manager.get_state_version()
            gateways = federated_manager.get_healthy_gateways()
            if any(sel in g.available_models for g in gateways):
                waited_ms = int((time.monotonic() - wait_start) * 1000)
                recovering_gw = next(
                    (g.gateway_id for g in gateways if sel in g.available_models),
                    "unknown",
                )
                if event_bus:
                    await _emit_event_safe(
                        event_bus,
                        RoutingModelGraceResolved(
                            request_id=context.request_id,
                            model_id=model_id,
                            gateway_id=recovering_gw,
                            waited_ms=waited_ms,
                        ),
                        "routing.model.grace.resolved",
                    )
                logger.info(
                    "Model gateway grace resolved model=%s waited_ms=%s gateway=%s",
                    model_id,
                    waited_ms,
                    recovering_gw,
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
            RoutingModelGraceTimeout(
                request_id=context.request_id,
                model_id=model_id,
                waited_ms=waited_ms,
            ),
            "routing.model.grace.timeout",
        )
    logger.warning(
        "Model gateway grace timeout for %s after %dms: no gateway recovered",
        model_id,
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
    capacity_pool: "CapacityPool | None" = None,
    routing_key_tracker: "RoutingKeyTracker | None" = None,
    starvation_drain_threshold_s: float = DEFAULT_STARVATION_DRAIN_THRESHOLD_S,
    drain_duration_s: float = DEFAULT_DRAIN_DURATION_S,
) -> tuple[Any, Any, int]:
    """
    Wait for federated state changes, then retry selection until success or timeout.

    When a waiter remains blocked by eviction_blocked_by_busy_models for longer
    than starvation_drain_threshold_s, we invoke the admission-drain primitive
    on the offending model(s): capacity_pool.pause_admission temporarily suspends
    new admissions for each tracker-busy routing_key on every busy-blocked
    candidate gateway (excluding the target). In-flight requests drain naturally
    on the gateway side — typically within one generation window — after which
    the routing key tracker reports no in-flight keys and the eviction planner
    succeeds on its next retry.

    If starvation persists past a drain window (e.g. in-flight requests hung
    or exceeding the initial drain_duration_s), the drain re-fires every
    drain_duration_s so the pause stays active across long waits rather than
    relying on an unrelated request to retrigger detection.
    """
    from src.scheduling.events.routing import (
        RoutingDrainInitiated,
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
    trace: Any = None
    last_drain_at: float | None = None

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
        while True:
            elapsed_s = time.monotonic() - wait_start
            if elapsed_s >= timeout_s:
                break

            state_version = federated_manager.get_state_version()

            fresh_gateways = [
                g
                for g in federated_manager.get_all_gateways()
                if g.dispatchable
            ]
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
                # First-iteration (or later) bail: wait entered because the
                # rejection-time classifier saw eviction_blocked_by_busy_models,
                # but the retry's trace no longer carries that constraint. Emit
                # the timeout signal with exit_reason="non_transient" and skip
                # the post-loop emission.
                waited_ms = int((time.monotonic() - wait_start) * 1000)
                if event_bus:
                    await _emit_event_safe(
                        event_bus,
                        RoutingEvictionWaitTimeout(
                            request_id=context.request_id,
                            model_id=str(placement.model_id),
                            waited_ms=waited_ms,
                            exit_reason="non_transient",
                            exit_constraint_summary=build_exit_constraint_summary(
                                trace
                            ),
                        ),
                        "routing.eviction.wait.timeout",
                    )
                return None, trace, waited_ms

            if (
                capacity_pool is not None
                and routing_key_tracker is not None
                and elapsed_s >= starvation_drain_threshold_s
                and (
                    last_drain_at is None
                    or (time.monotonic() - last_drain_at) >= drain_duration_s
                )
            ):
                drain_fired = _trigger_starvation_drain(
                    capacity_pool=capacity_pool,
                    routing_key_tracker=routing_key_tracker,
                    trace=trace,
                    placement=placement,
                    context=context,
                    event_bus=event_bus,
                    drain_duration_s=drain_duration_s,
                    starved_for_ms=int(elapsed_s * 1000),
                    drain_event_factory=RoutingDrainInitiated,
                )
                if drain_fired:
                    last_drain_at = time.monotonic()

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
                exit_reason="budget_exhausted",
                exit_constraint_summary=build_exit_constraint_summary(trace),
            ),
            "routing.eviction.wait.timeout",
        )
    return None, trace, waited_ms


def _trigger_starvation_drain(
    *,
    capacity_pool: "CapacityPool",
    routing_key_tracker: "RoutingKeyTracker",
    trace: Any,
    placement: Any,
    context: "RequestContext",
    event_bus: "EventBus | None",
    drain_duration_s: float,
    starved_for_ms: int,
    drain_event_factory: Any,
) -> bool:
    """
    Pause admission on each in-flight routing_key blocking the target placement.

    Computes the drain set from the selection trace: for every candidate gateway
    reporting eviction_blocked_by_busy_models, enumerate routing_keys currently
    in-flight there and pause admission for each (minus the target model, which
    must never be drained against itself). Returns True iff at least one pause
    was applied. The routing_key tracker is the authoritative source for what
    is blocking eviction — using it here ensures we pause exactly the set the
    eviction planner is filtering out.
    """
    target_key = placement.model_id.routing_key

    busy_blocked_gateways: list[str] = []
    for candidate in trace.candidates if trace else []:
        is_blocked = any(
            f.constraint == "eviction_blocked_by_busy_models"
            for f in candidate.constraints_failed
        )
        if is_blocked:
            busy_blocked_gateways.append(candidate.gateway.name)

    if not busy_blocked_gateways:
        return False

    drained_keys: set[str] = set()
    for gateway_name in busy_blocked_gateways:
        in_flight_keys = routing_key_tracker.get_routing_keys_in_flight(gateway_name)
        for routing_key in in_flight_keys:
            if routing_key == target_key:
                continue
            capacity_pool.pause_admission(
                routing_key,
                duration_s=drain_duration_s,
                reason="starvation_relief",
            )
            drained_keys.add(routing_key)

    if not drained_keys:
        return False

    logger.warning(
        "Starvation drain INITIATED for %s: paused %d model(s) across %d gateway(s) "
        "after %dms; drained=%s",
        placement.model_id,
        len(drained_keys),
        len(busy_blocked_gateways),
        starved_for_ms,
        sorted(drained_keys),
    )

    if event_bus:
        try:
            asyncio.create_task(
                event_bus.publish_nowait(
                    drain_event_factory(
                        request_id=context.request_id,
                        target_model_id=str(placement.model_id),
                        gateway_ids=busy_blocked_gateways,
                        drained_model_ids=sorted(drained_keys),
                        duration_s=drain_duration_s,
                        starved_for_ms=starved_for_ms,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit routing.drain.initiated: %s", exc)

    return True
