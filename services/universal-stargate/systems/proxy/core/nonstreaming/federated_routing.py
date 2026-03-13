"""
Federated gateway routing for Master mode.

Handles gateway selection, model loading, and routing events.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import ErrorCode

from systems.federation.common.config.schema import EndpointCategory

from ..endpoint_category import derive_endpoint_category
from .selection_errors import (
    raise_all_gateways_excluded_error,
    raise_capacity_error,
    raise_eviction_failed_error,
    raise_gateway_capacity_error,
    raise_load_failed_error,
    raise_model_unavailable_error,
    raise_no_feasible_gateway_error,
    raise_no_gateways_error,
)

if TYPE_CHECKING:
    from model_id import ModelId
    from universal_event_bus import EventBus

    from systems.federation.common.types import FederatedGateway
    from systems.federation.master.circuit_breaker import FederationCircuitBreaker
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.orchestration.load_orchestrator import (
        FederatedLoadOrchestrator,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.federation.master.routing.orchestrator import MasterRequestTracker
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision.stability import StickyPlacementTracker

    from .context import RequestContext

logger = get_logger(__name__)

# Eviction wait queue depth (for routing.eviction.wait.started payload and monitoring)
_eviction_wait_queue_depth: int = 0


async def _emit_event_safe(event_bus: "EventBus", event: Any, event_name: str) -> None:
    """Emit event with debug-level failure logging."""
    try:
        await event_bus.publish_async_nowait(event)
    except Exception as exc:
        logger.debug(f"Failed to emit {event_name} event: {exc}")


async def _emit_routing_resource_gap_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    federated_gateways: list["FederatedGateway"],
) -> None:
    """
    Emit routing.resource.data.missing if model is in catalog but has no resource data.

    When model_id is in a gateway's available_models but not in its model_resources,
    routing fails with missing_gateway_resource_data — not because the model is
    absent, but because resource data wasn't populated yet (startup gap).

    This event distinguishes that case from genuine MODEL_NOT_FOUND.
    """
    from src.scheduling.events import RoutingResourceDataMissing

    gap_gateway_ids = [
        fg.gateway_id
        for fg in federated_gateways
        if model_id in fg.available_models and model_id not in fg.model_resources
    ]
    for gateway_id in gap_gateway_ids:
        await _emit_event_safe(
            event_bus,
            RoutingResourceDataMissing(
                request_id=request_id,
                model_id=str(model_id),
                gateway_ids=[gateway_id],
            ),
            f"routing.resource.data.missing:{gateway_id}",
        )


def _build_constraint_summary(
    trace: Any | None,
    federated_gateways: list["FederatedGateway"],
    context: "RequestContext",
) -> dict[str, Any]:
    """Build per-gateway constraint failure summary for error envelope."""
    summary: dict[str, Any] = {}
    if context.excluded_gateway_ids:
        summary["excluded_gateways"] = list(context.excluded_gateway_ids)
    if trace and trace.candidates:
        summary["gateway_failures"] = [
            {
                "gateway": c.gateway.name,
                "constraints": [
                    {"constraint": f.constraint, "reason": f.reason}
                    for f in c.constraints_failed
                ],
            }
            for c in trace.candidates
            if c.constraints_failed
        ]
    return summary


async def _emit_routing_model_infeasible_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    trace: Any | None,
    excluded_gateway_ids: list[str],
) -> None:
    """
    Emit routing.model.infeasible when model exists but all gateways are infeasible.
    """
    from src.scheduling.events import RoutingModelInfeasible

    gateway_constraints: list[dict[str, Any]] = []
    if trace and trace.candidates:
        gateway_constraints = [
            {
                "gateway": c.gateway.name,
                "constraints": [
                    {"constraint": f.constraint, "reason": f.reason}
                    for f in c.constraints_failed
                ],
            }
            for c in trace.candidates
            if c.constraints_failed
        ]

    await _emit_event_safe(
        event_bus,
        RoutingModelInfeasible(
            request_id=request_id,
            model_id=str(model_id),
            gateway_constraints=gateway_constraints,
            excluded_gateway_ids=excluded_gateway_ids,
        ),
        "routing.model.infeasible",
    )


async def _emit_eviction_classification_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    trace: Any | None,
    classification: str,
    failure_reason: str,
) -> None:
    """Emit granular routing eviction classification event."""
    from src.scheduling.events import (
        RoutingEvictionBlockedBusy,
        RoutingEvictionInsufficientPermanent,
    )

    gateway_id = "unknown"
    loaded_count = 0
    busy_count = 0
    vram_free = 0
    failed_constraints: list[str] = []

    if trace and trace.candidates:
        target_constraint = (
            "eviction_blocked_by_busy_models"
            if classification == "busy_blocked"
            else "can_fit_with_eviction"
        )
        selected = next(
            (
                c
                for c in trace.candidates
                if any(f.constraint == target_constraint for f in c.constraints_failed)
            ),
            trace.candidates[0],
        )
        gateway_id = selected.gateway.name
        loaded_count = len(selected.gateway.loaded_models)
        busy_count = len(selected.gateway.busy_models)
        vram_free = selected.gateway.vram_free_mb
        failed_constraints = [f.constraint for f in selected.constraints_failed]

    if classification == "busy_blocked":
        await _emit_event_safe(
            event_bus,
            RoutingEvictionBlockedBusy(
                request_id=request_id,
                model_id=str(model_id),
                gateway_id=gateway_id,
                loaded_count=loaded_count,
                busy_count=busy_count,
                vram_free=vram_free,
            ),
            "routing.eviction.blocked.busy",
        )
    elif classification == "permanent_insufficient":
        await _emit_event_safe(
            event_bus,
            RoutingEvictionInsufficientPermanent(
                request_id=request_id,
                model_id=str(model_id),
                gateway_id=gateway_id,
                reason=failure_reason,
                failed_constraints=failed_constraints,
            ),
            "routing.eviction.insufficient.permanent",
        )


async def _emit_overflow_triggered_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    from_gateway: str,
    to_gateway: str,
    reason: str,
) -> None:
    """Emit routing.overflow.triggered for non-sticky spillover attempts."""
    from src.scheduling.events.routing import RoutingOverflowTriggered

    await _emit_event_safe(
        event_bus,
        RoutingOverflowTriggered(
            request_id=request_id,
            model_id=str(model_id),
            from_gateway=from_gateway,
            to_gateway=to_gateway,
            reason=reason,
        ),
        "routing.overflow.triggered",
    )


async def _emit_overflow_failed_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    tried_gateways: list[str],
    reason: str,
) -> None:
    """Emit routing.overflow.failed when no alternate is feasible."""
    from src.scheduling.events.routing import RoutingOverflowFailed

    await _emit_event_safe(
        event_bus,
        RoutingOverflowFailed(
            request_id=request_id,
            model_id=str(model_id),
            tried_gateways=sorted(tried_gateways),
            reason=reason,
        ),
        "routing.overflow.failed",
    )


async def _emit_overflow_load_started_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    gateway_id: str,
    reason: str,
) -> None:
    """Emit model.load.overflow.started before overflow cold-load begins."""
    from src.scheduling.events.routing import ModelLoadOverflowStarted

    await _emit_event_safe(
        event_bus,
        ModelLoadOverflowStarted(
            request_id=request_id,
            model_id=str(model_id),
            gateway_id=gateway_id,
            reason=reason,
        ),
        "model.load.overflow.started",
    )


async def _emit_overflow_assigned_event(
    event_bus: "EventBus",
    request_id: str,
    model_id: "ModelId",
    from_gateway: str,
    to_gateway: str,
    depth_before: int,
) -> None:
    """Emit model.capacity.overflow.assigned at admission boundary."""
    from src.scheduling.events.routing import ModelCapacityOverflowAssigned

    await _emit_event_safe(
        event_bus,
        ModelCapacityOverflowAssigned(
            request_id=request_id,
            model_id=str(model_id),
            from_gateway=from_gateway,
            to_gateway=to_gateway,
            depth_before=depth_before,
        ),
        "model.capacity.overflow.assigned",
    )


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
    """Wait for model state changes, then re-run selection.

    Epoch-before-select pattern: record generation → select → if fail, wait
    for generation bump → repeat. Guarantees no missed wake-ups.
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


async def _route_to_federated_gateway(
    context: "RequestContext",
    federated_manager: "FederatedGatewayManager | None",
    federated_load_orchestrator: "FederatedLoadOrchestrator | None",
    federation_forwarder: "FederatedRequestForwarder | None",
    event_bus: "EventBus | None",
    routing_start_time: float,
    routing_config: dict[str, Any] | None = None,
    stability_tracker: "StickyPlacementTracker | None" = None,
    compute_type_tracker: "MasterRequestTracker | None" = None,
    routing_key_tracker: "MasterRequestTracker | None" = None,
    capacity_pool: "CapacityPool | None" = None,
    circuit_breaker: "FederationCircuitBreaker | None" = None,
) -> tuple[str | None, str | None]:
    """
    Router-only mode: Select and load model on federated gateway.

    Args:
        context: Request context
        federated_manager: FederatedGatewayManager for getting remote gateways
        federated_load_orchestrator: For loading models on remotes
        federation_forwarder: Forwarder for sending eviction commands (may be None)
        event_bus: Event bus for routing events
        routing_start_time: Timestamp when routing started
        routing_config: Full Stargate config dict for loading routing policy
        compute_type_tracker: MasterRequestTracker for compute-type limits
        routing_key_tracker: MasterRequestTracker for eviction protection
        capacity_pool: CapacityPool for admission control
        circuit_breaker: FederationCircuitBreaker for availability checks

    Returns:
        Tuple of (gateway_name, reservation_id) if selected, (None, None) otherwise

    Raises:
        HTTPException: If no gateway available or model not found
    """
    from systems.routing.selection.types import Placement

    model_id = context.selected_model

    # Use pre-set endpoint category if available (e.g., embedding requests
    # with no http_request). Otherwise derive from request path.
    if context.routing_endpoint_category is not None:
        endpoint_category = context.routing_endpoint_category
        logger.debug(f"Using pre-set endpoint category: {endpoint_category.value}")
    else:
        try:
            endpoint_category = derive_endpoint_category(request=context.http_request)
        except ValueError:
            # Fallback for unknown paths - log at ERROR per quality-gates policy
            logger.error(
                "❌ Could not derive endpoint category from request, "
                "defaulting to generation (this may cause capacity tracking issues)"
            )
            endpoint_category = EndpointCategory.GENERATION

        # Store in context for consistent use during forwarding
        # CRITICAL: Prevents leak when reservation category differs from forward
        context.routing_endpoint_category = endpoint_category

    # Timing marker: routing start
    selection_start_ms = int(time.time() * 1000)
    logger.info(f"ROUTING START: {model_id} at {selection_start_ms}ms")

    logger.info(f"🔍 Router-only: selecting federated gateway for {model_id}")

    # INV-2: stability_tracker is REQUIRED - fail loudly if missing
    if stability_tracker is None:
        raise ValueError(
            "stability_tracker is required for routing stability. "
            "Ensure component_factory properly initializes StickyPlacementTracker."
        )

    # Get federated gateways
    all_gateways = federated_manager.get_all_gateways()
    logger.info(f"🔍 Router-only: Total gateways registered: {len(all_gateways)}")
    for gw in all_gateways:
        logger.info(
            f"  - {gw.gateway_id}: age={gw.telemetry_age_ms}ms, "
            f"unreachable={gw.is_unreachable}"
        )

    federated_gateways = federated_manager.get_healthy_gateways()
    logger.info(f"🔍 Router-only: Healthy gateways: {len(federated_gateways)}")
    if not federated_gateways:
        logger.error("No federated gateways available in router-only mode")
        raise_no_gateways_error()

    # Convert FederatedGateway to Gateway for DecisionEngine
    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )

    gateways_for_routing = federated_gateways_to_routing_candidates(federated_gateways)

    # Exclude gateways that returned upstream (5xx) errors on previous retry.
    # ∀ upstream failure: excluded gateways ⊇ gateways_with_model
    #   ⟹ fail immediately (no alternative — retrying same gateway wastes budget)
    # ∀ upstream failure: ∃ alternative gateway ⟹ apply exclusion and re-route
    if context.excluded_gateway_ids:
        kept = [
            g
            for g in gateways_for_routing
            if g.name not in context.excluded_gateway_ids
        ]
        has_model_alternative = any(
            model_id in g.available_models or model_id in g.loaded_models for g in kept
        )
        if not kept or not has_model_alternative:
            # All gateways with the model have already returned upstream errors.
            # Bypassing exclusion would retry on the same failed gateway —
            # instead emit an event and fail non-retryably.
            logger.warning(
                "🚫 All gateways for %s excluded after upstream failures: %s",
                model_id,
                context.excluded_gateway_ids,
            )
            if event_bus:
                import asyncio

                from src.scheduling.events import RoutingUpstreamAllExcluded

                asyncio.create_task(
                    event_bus.publish_async_nowait(
                        RoutingUpstreamAllExcluded(
                            request_id=context.request_id,
                            model_id=str(model_id),
                            excluded_gateway_ids=list(context.excluded_gateway_ids),
                        )
                    )
                )
            raise_all_gateways_excluded_error(
                str(model_id),
                list(context.excluded_gateway_ids),
                upstream_errors=context.excluded_gateway_errors or None,
            )
        logger.info("🚫 Routing: excluded %s", context.excluded_gateway_ids)
        gateways_for_routing = kept

    # Exclude gateways with persistent load failures for this model+context
    load_failed_ids = [
        g.name
        for g in gateways_for_routing
        if federated_manager.is_load_failed(g.name, model_id)
    ]
    if load_failed_ids:
        eligible = [g for g in gateways_for_routing if g.name not in load_failed_ids]
        if not eligible:
            raise_load_failed_error(str(model_id), load_failed_ids)
        logger.info("🚫 Filtered load-failed gateways: %s", load_failed_ids)
        gateways_for_routing = eligible

    logger.debug(
        f"Router-only: {len(gateways_for_routing)} federated gateways available"
    )

    # Build placement hint from first matching gateway's model_resources.
    # This is a hint only — per-gateway authoritative figures are resolved
    # in _check_resources() via resolve_gateway_requirements().
    vram_mb = 0
    ram_mb = 0
    for fg in federated_gateways:
        if model_id in fg.model_resources:
            resources = fg.model_resources[model_id]
            vram_mb = resources.get("vram_usage", 0)
            ram_mb = resources.get("ram_usage", 0)
            break

    placement = Placement(
        model_id=model_id,
        ram_mb=ram_mb,
        vram_mb=vram_mb,
        is_gpu=vram_mb > 0,
        endpoint_category=endpoint_category.value,
    )

    logger.info(
        f"📋 Router-only: Placement hint for {model_id}: "
        f"VRAM={placement.vram_mb}MB, RAM={placement.ram_mb}MB, "
        f"is_gpu={placement.is_gpu} (per-gateway figures resolved at check time)"
    )

    # Create DecisionEngine (stateless, can be per-request OK)
    # INV-1: routing_config must be FULL config dict
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.config import load_routing_policy

    from .routing_wait import has_demand_for

    policy = load_routing_policy(routing_config or {})

    # Create availability callback for circuit breaker
    is_gateway_available_fn = None
    if circuit_breaker:
        is_gateway_available_fn = circuit_breaker.is_request_allowed_sync

    # Eviction hysteresis config
    eviction_cooldown_s: float = float(
        (routing_config or {}).get("routing", {}).get("eviction_cooldown_s", 120.0)
    )

    decision_engine = DecisionEngine(
        policy=policy,
        event_bus=event_bus,
        routing_key_tracker=routing_key_tracker,
        is_gateway_available_fn=is_gateway_available_fn,
        eviction_cooldown_s=eviction_cooldown_s,
        has_demand=has_demand_for,
    )

    # Use DecisionEngine to select gateway
    # Log detailed gateway state for nonsticky debugging
    for g in gateways_for_routing:
        logger.info(
            f"Gateway {g.name}: loaded={len(g.loaded_models)}, "
            f"loading={len(g.loading_models)}, "
            f"target_model_loading={model_id in g.loading_models}"
        )

    logger.info(
        f"📋 Router-only candidates: {len(gateways_for_routing)} gateways, "
        f"empty={sum(1 for g in gateways_for_routing if len(g.loaded_models) + len(g.loading_models) == 0)}, "  # noqa: E501
        f"with_model={sum(1 for g in gateways_for_routing if model_id in g.loaded_models)}, "  # noqa: E501
        f"loading_model={sum(1 for g in gateways_for_routing if model_id in g.loading_models)}"  # noqa: E501
    )

    selected_gateway, trace = decision_engine.select(
        gateways=gateways_for_routing,
        placement=placement,
        request_id=context.request_id,
        sticky=context.model_sticky,
        stability_tracker=stability_tracker,
    )

    allowed_gateway_ids_override: frozenset[str] | None = None
    overflow_origin_gateway: str | None = None
    overflow_depth_before = 0

    # Non-sticky overflow: if selected gateway has no immediate slot, try a
    # second decision pass excluding the primary gateway.
    if (
        selected_gateway is not None
        and not context.model_sticky
        and policy.non_sticky_overflow_enabled
        and capacity_pool is not None
    ):
        primary_available, primary_in_flight, primary_capacity = (
            capacity_pool.get_slot_info(selected_gateway.name, model_id.routing_key)
        )
        queue_pressure = max(0, primary_in_flight - primary_capacity)
        primary_saturated = primary_capacity > 0 and primary_available <= 0
        queue_over_threshold = (
            queue_pressure >= policy.non_sticky_overflow_queue_threshold
        )

        if primary_saturated or queue_over_threshold:
            overflow_origin_gateway = selected_gateway.name
            overflow_depth_before = queue_pressure
            overflow_gateway, overflow_trace = decision_engine.select_excluding(
                gateways=gateways_for_routing,
                placement=placement,
                excluded_gateway_names=frozenset({selected_gateway.name}),
                request_id=context.request_id,
                sticky=context.model_sticky,
                stability_tracker=stability_tracker,
            )

            if overflow_gateway is None:
                if event_bus:
                    tried_gateways = [
                        g.name
                        for g in gateways_for_routing
                        if g.name != selected_gateway.name
                    ]
                    await _emit_overflow_failed_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        tried_gateways=tried_gateways,
                        reason=(
                            overflow_trace.selection_reason
                            if overflow_trace
                            else "no_alternate_gateway"
                        ),
                    )
            else:
                if event_bus:
                    await _emit_overflow_triggered_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        from_gateway=selected_gateway.name,
                        to_gateway=overflow_gateway.name,
                        reason=(
                            "queue_threshold_exceeded"
                            if queue_over_threshold
                            else "primary_capacity_saturated"
                        ),
                    )

                try:
                    if (
                        federated_load_orchestrator
                        and model_id not in overflow_gateway.loaded_models
                        and model_id not in overflow_gateway.loading_models
                    ):
                        if event_bus:
                            await _emit_overflow_load_started_event(
                                event_bus=event_bus,
                                request_id=context.request_id,
                                model_id=model_id,
                                gateway_id=overflow_gateway.name,
                                reason="overflow_spillover",
                            )
                        await federated_load_orchestrator.ensure_model_loaded_on_remote(
                            overflow_gateway.ref,
                            model_id,
                            sticky=False,
                            request_id=context.request_id,
                        )

                    selected_gateway = overflow_gateway
                    allowed_gateway_ids_override = frozenset(
                        g.name
                        for g in gateways_for_routing
                        if model_id in g.loaded_models
                    ) | frozenset({overflow_gateway.name})
                except Exception as exc:
                    logger.warning(
                        "Overflow load attempt failed for %s on %s: %s",
                        model_id,
                        overflow_gateway.name,
                        exc,
                    )
                    if event_bus:
                        await _emit_overflow_failed_event(
                            event_bus=event_bus,
                            request_id=context.request_id,
                            model_id=model_id,
                            tried_gateways=[overflow_gateway.name],
                            reason="overflow_load_failed",
                        )

    if not selected_gateway and event_bus:
        from src.scheduling.events.routing import (
            RoutingDequeued,
            RoutingQueued,
            RoutingTimeout,
        )

        from .routing_wait import (
            QUEUE_TIMEOUT_S,
            extract_retryable_constraint,
            wait_for_capacity_signal,
        )

        retryable_constraint = extract_retryable_constraint(trace)
        if retryable_constraint:
            queue_start = time.monotonic()
            deadline = queue_start + QUEUE_TIMEOUT_S

            await event_bus.publish_async_nowait(
                RoutingQueued(
                    request_id=context.request_id,
                    model_id=str(model_id),
                    constraint=retryable_constraint,
                    timestamp=time.time(),
                )
            )

            logger.info(
                "⏳ Pre-routing queue: %s waiting for capacity "
                "(constraint=%s, budget=%.1fs)",
                model_id,
                retryable_constraint,
                QUEUE_TIMEOUT_S,
            )

            while not selected_gateway and time.monotonic() < deadline:
                try:
                    signaled = await wait_for_capacity_signal(
                        event_bus=event_bus,
                        model_id=model_id.routing_key,
                        request_id=context.request_id,
                        deadline=deadline,
                    )
                except Exception as exc:
                    logger.warning(
                        "Pre-routing queue wait failed for %s: %s",
                        model_id,
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
                            model_id=str(model_id),
                            gateway_id=selected_gateway.name,
                            wait_ms=wait_ms,
                            timestamp=time.time(),
                        )
                    )
                    logger.info(
                        "✅ Pre-routing dequeued: %s -> %s after %.0fms",
                        model_id,
                        selected_gateway.name,
                        wait_ms,
                    )
                    break

                if not signaled and time.monotonic() >= deadline:
                    break

            if not selected_gateway:
                wait_ms = (time.monotonic() - queue_start) * 1000.0
                await event_bus.publish_async_nowait(
                    RoutingTimeout(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        constraint=retryable_constraint,
                        wait_ms=wait_ms,
                        timestamp=time.time(),
                    )
                )
                logger.warning(
                    "⏳ Pre-routing timeout: %s after %.0fms (constraint=%s)",
                    model_id,
                    wait_ms,
                    retryable_constraint,
                )

    # Admission control: acquire slot before proceeding.
    # Non-sticky overflow may perform a bounded pre-admission load on an
    # alternate gateway before this point.
    is_cold_load = (
        selected_gateway is not None and model_id not in selected_gateway.loaded_models
    )

    # Pre-seed CapacityPool for cold loads so admission gates the burst that
    # hits the gateway once the model finishes loading.  model_details contains
    # max_concurrent_requests for ALL catalog models (including unloaded ones),
    # populated from model_resources via the collector.
    if is_cold_load and capacity_pool and selected_gateway:
        details = selected_gateway.model_details.get(model_id, {})
        expected_capacity = int(details.get("max_concurrent_requests", 1))
        capacity_pool.set_capacity(
            selected_gateway.name,
            model_id.routing_key,
            expected_capacity,
        )
        logger.info(
            f"📊 Cold-load capacity pre-seed: {selected_gateway.name}/"
            f"{model_id.routing_key} → {expected_capacity} slots"
        )
        if event_bus:
            import asyncio

            from src.scheduling.events import RoutingCapacityPreseeded

            asyncio.create_task(
                event_bus.publish_async_nowait(
                    RoutingCapacityPreseeded(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        expected_capacity=expected_capacity,
                    )
                )
            )

    # Detect busy_models / CapacityPool divergence for observability.
    if event_bus and capacity_pool and selected_gateway and not is_cold_load:
        pool_available, pool_in_flight, pool_capacity = capacity_pool.get_slot_info(
            selected_gateway.name,
            model_id.routing_key,
        )
        is_busy_per_telemetry = model_id in selected_gateway.busy_models
        if is_busy_per_telemetry and pool_available > 0:
            import asyncio

            from src.scheduling.events import RoutingCapacityDivergence

            asyncio.create_task(
                event_bus.publish_async_nowait(
                    RoutingCapacityDivergence(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        busy_models_state="busy",
                        capacity_pool_available=pool_available,
                        capacity_pool_in_flight=pool_in_flight,
                        capacity_pool_max=pool_capacity,
                    )
                )
            )

    # STICKY INVARIANT: ∀ sticky model_id, ∃ healthy gateway G with model
    # loaded|loading ⟹ route to G, ¬cold_load elsewhere.
    # Prevents duplicate loads that waste VRAM and may trigger unnecessary evictions.
    if context.model_sticky and is_cold_load and selected_gateway:
        already_warm = next(
            (
                g
                for g in gateways_for_routing
                if g.name != selected_gateway.name
                and (model_id in g.loaded_models or model_id in g.loading_models)
            ),
            None,
        )
        if already_warm:
            warm_state = (
                "loaded" if model_id in already_warm.loaded_models else "loading"
            )
            logger.info(
                f"📍 Sticky guard: {model_id} already {warm_state} on "
                f"{already_warm.name}, redirecting from cold-load on "
                f"{selected_gateway.name}"
            )
            selected_gateway = already_warm
            is_cold_load = model_id not in selected_gateway.loaded_models
            stability_tracker.update_binding(model_id, selected_gateway.name)

    if selected_gateway and capacity_pool:
        # Sticky: honour the engine's exact selection.
        # ∀ sticky: allowed ⊆ {selected_gateway} so admission cannot reassign.
        # Non-sticky warm: allow any warm gateway for load balancing.
        # Cold load: restrict to selected gateway (it's the load target).
        if allowed_gateway_ids_override is not None:
            allowed_gateway_ids = allowed_gateway_ids_override
        elif context.model_sticky or is_cold_load:
            allowed_gateway_ids = frozenset({selected_gateway.name})
        else:
            allowed_gateway_ids = frozenset(
                g.name for g in gateways_for_routing if model_id in g.loaded_models
            )

        # Default: no timeout — wait indefinitely for a slot.
        # ∀ sticky models with all slots occupied: the outer pipeline step timeout
        # governs the deadline; a short admission timeout causes requests to lose
        # their FIFO queue position, re-enter from scratch, and spin for the full
        # step budget without making progress.
        # Explicit admission.timeout_s in config enables early fail-fast behaviour.
        timeout_s = (
            routing_config.get("admission", {}).get("timeout_s", None)
            if routing_config
            else None
        )

        try:
            token = await capacity_pool.acquire_token(
                request_id=context.request_id,
                model_id=model_id.routing_key,
                allowed_gateway_ids=allowed_gateway_ids,
                timeout_s=timeout_s,
            )
            context.capacity_token = token
            if (
                event_bus
                and overflow_origin_gateway is not None
                and token.gateway_id != overflow_origin_gateway
            ):
                await _emit_overflow_assigned_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=model_id,
                    from_gateway=overflow_origin_gateway,
                    to_gateway=token.gateway_id,
                    depth_before=overflow_depth_before,
                )

            # If assigned to a different gateway than selected, re-select
            if token.gateway_id != selected_gateway.name:
                original_gateway_name = selected_gateway.name
                selected_gateway = next(
                    (g for g in gateways_for_routing if g.name == token.gateway_id),
                    selected_gateway,
                )
                logger.info(
                    f"📊 Admission control reassigned {model_id} from "
                    f"{original_gateway_name} → {token.gateway_id}"
                )
        except TimeoutError:
            logger.warning(
                f"⏳ Admission queue timeout: model={model_id.routing_key} "
                f"gateway={selected_gateway.name} timeout_s={timeout_s} "
                f"allowed_gateways={sorted(allowed_gateway_ids)}"
            )
            # Do NOT clear_binding here — the gateway is correct; it is temporarily
            # at capacity. Clearing causes the STICKY GUARD to fire on the next retry
            # (engine picks a different gateway, guard blocks it) producing a spin loop.
            raise_gateway_capacity_error(selected_gateway.name)
        except Exception as e:
            logger.error(
                (
                    "❌ Admission queue acquire failed for model=%s "
                    "gateway=%s request_id=%s: %s"
                ),
                model_id.routing_key,
                selected_gateway.name,
                context.request_id,
                e,
            )
            stability_tracker.clear_binding(model_id)
            raise_gateway_capacity_error(selected_gateway.name)

    # Emit orchestrator decision event
    if event_bus:
        import asyncio

        from src.scheduling.events import FederationOrchestratorDecided

        decision_type = "route" if selected_gateway else "reject"
        target = selected_gateway.name if selected_gateway else None
        reason = (
            f"Selected {selected_gateway.name} (tier={trace.selection_tier.name})"
            if selected_gateway
            else "No feasible gateway available"
        )
        alternatives = (
            [g.name for g in gateways_for_routing[:5]] if gateways_for_routing else []
        )

        asyncio.create_task(
            event_bus.publish_async_nowait(
                FederationOrchestratorDecided(
                    request_id=context.request_id,
                    decision_type=decision_type,
                    target=target,
                    reason=reason,
                    alternatives_considered=alternatives if alternatives else None,
                )
            )
        )

    # CRITICAL: Synchronous optimistic mark - no await, immediate visibility
    # Must happen before ANY await to prevent concurrent select() seeing stale state
    marked_loading = False
    optimistic_mark_gateway_id = None
    optimistic_mark_model_id = None

    if (
        selected_gateway
        and federated_manager
        and model_id not in selected_gateway.loaded_models
    ):
        marked_loading = federated_manager.mark_loading_optimistic(
            selected_gateway.ref.gateway_id, model_id
        )
        if marked_loading:
            optimistic_mark_gateway_id = selected_gateway.ref.gateway_id
            optimistic_mark_model_id = model_id

    # Timing markers AFTER marking (so concurrent requests see the mark)
    selection_end_ms = int(time.time() * 1000)
    if marked_loading:
        logger.info(
            f"ROUTING+MARK: {model_id} → {selected_gateway.name} "
            f"at {selection_end_ms}ms "
            f"(took {selection_end_ms - selection_start_ms}ms, marked loading)"
        )
    else:
        logger.info(
            f"ROUTING END: {model_id} selected "
            f"{selected_gateway.name if selected_gateway else 'NONE'} "
            f"at {selection_end_ms}ms (took {selection_end_ms - selection_start_ms}ms)"
        )

    if not selected_gateway:
        logger.error(f"No feasible federated gateway for {model_id}")

        # Emit routing rejected event
        if event_bus:
            import asyncio

            from src.scheduling.events import FederationRoutingRejected

            asyncio.create_task(
                event_bus.publish_async_nowait(
                    FederationRoutingRejected(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        reason="No feasible gateway available",
                    )
                )
            )

        # For sticky models: Check if we should wait (model at capacity)
        # vs error (model doesn't exist or hardware can't serve it)
        if context.model_sticky and trace and trace.candidates:
            # Always-transient: retrying will eventually help
            transient_constraints: frozenset[str] = frozenset(
                {
                    "compute_type_capacity",
                    # circuit_breaker: OPEN→HALF_OPEN after recovery_timeout.
                    # Model IS available; gateway is isolated. Not permanent.
                    "circuit_breaker",
                    # All loaded models are busy; once in-flight requests complete,
                    # those models become idle and can be evicted to make room.
                    "eviction_blocked_by_busy_models",
                }
            )
            # VRAM/RAM failures are transient only when eviction CAN free space.
            # When can_fit_with_eviction also fails, no idle models exist that
            # could be evicted — hardware limit, not a transient wait condition.
            resource_constraints: frozenset[str] = frozenset(
                {
                    "has_enough_vram",
                    "has_enough_ram",
                }
            )

            def _is_transient_capacity_failure(c: Any) -> bool:
                failed = {f.constraint for f in c.constraints_failed}
                if failed & transient_constraints:
                    return True
                if failed & resource_constraints:
                    return "can_fit_with_eviction" in failed
                return False

            has_capacity_failure = any(
                _is_transient_capacity_failure(c) for c in trace.candidates
            )
            has_busy_eviction_block = any(
                any(
                    f.constraint == "eviction_blocked_by_busy_models"
                    for f in c.constraints_failed
                )
                for c in trace.candidates
            )

            # Permanent resource failure: VRAM or RAM insufficient even after
            # evicting everything. Fail fast — no point waiting.
            # Condition: (has_enough_vram ∨ has_enough_ram) ∧ can_fit_with_eviction
            # both failed on the same candidate.
            def _is_permanent_resource_failure(c: Any) -> bool:
                failed = {f.constraint for f in c.constraints_failed}
                return "can_fit_with_eviction" in failed and bool(
                    failed & resource_constraints
                )

            has_permanent_resource_failure = any(
                _is_permanent_resource_failure(c) for c in trace.candidates
            )
            if has_permanent_resource_failure and not has_capacity_failure:
                from .selection_errors import raise_insufficient_resources_error

                failure_reason = next(
                    (
                        f.reason
                        for c in trace.candidates
                        for f in c.constraints_failed
                        if f.constraint in resource_constraints
                    ),
                    "VRAM/RAM insufficient to load model",
                )
                logger.warning(
                    f"❌ Permanent resource failure for {model_id}: {failure_reason}"
                )
                if event_bus:
                    await _emit_eviction_classification_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        trace=trace,
                        classification="permanent_insufficient",
                        failure_reason=failure_reason,
                    )
                    await _emit_routing_model_infeasible_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        trace=trace,
                        excluded_gateway_ids=list(context.excluded_gateway_ids),
                    )
                raise_insufficient_resources_error(str(model_id), failure_reason)

            if has_capacity_failure:
                # Find details for error envelope data
                capacity_gateway_url = None
                capacity_details: dict[str, Any] = {"model_id": str(model_id)}

                # Extract capacity details from failures
                _all_capacity = transient_constraints | resource_constraints
                for c in trace.candidates:
                    for f in c.constraints_failed:
                        if f.constraint in _all_capacity:
                            capacity_details.update(f.details)
                            break

                # Find gateway URL for wait monitoring
                # Check loaded_models first (model already running)
                # Then check catalog_models (model exists but not loaded yet)
                logger.debug(
                    f"🔍 Searching for {model_id} (type={type(model_id).__name__}, "
                    f"repr={repr(model_id)}) across {len(federated_gateways)} gateways"
                )
                for fg in federated_gateways:
                    logger.debug(
                        f"  Gateway {fg.gateway_id}: "
                        f"loaded={len(fg.loaded_models)}, "
                        f"available={len(fg.available_models)}"
                    )
                    if model_id in fg.loaded_models:
                        capacity_gateway_url = fg.remote_stargate_url
                        capacity_details["gateway_url"] = capacity_gateway_url
                        logger.debug(
                            f"Found loaded model {model_id} on {fg.gateway_id} "
                            f"(URL: {capacity_gateway_url})"
                        )
                        break

                if not capacity_gateway_url:
                    # Model not loaded - check available_models (catalog)
                    # available_models = ALL models in catalog that CAN be loaded
                    for fg in federated_gateways:
                        if model_id in fg.available_models:
                            capacity_gateway_url = fg.remote_stargate_url
                            capacity_details["gateway_url"] = capacity_gateway_url
                            logger.debug(
                                f"Found cataloged model {model_id} on {fg.gateway_id} "
                                f"(URL: {capacity_gateway_url}, not yet loaded)"
                            )
                            break
                        else:
                            # Diagnostic: show why not found
                            if fg.available_models:
                                sample = list(fg.available_models)[:3]
                                sample_repr = [repr(m) for m in sample]
                                logger.debug(
                                    f"  {fg.gateway_id}: model not in "
                                    f"available_models. Sample: {sample_repr}"
                                )
                            else:
                                logger.debug(
                                    f"  {fg.gateway_id}: available_models is empty"
                                )

                # Model exists but at capacity - raise with error_envelope
                # NOTE: With proactive queueing, this path is only hit when:
                # 1. Model was just loaded (no queue existed yet)
                # 2. TOCTOU race between queue and routing
                if not capacity_gateway_url:
                    logger.error(
                        f"❌ BUG: Capacity constraint failed for {model_id} but "
                        f"gateway_url not found. This should not happen if model "
                        f"exists in available_models. Check diagnostic logs above."
                    )
                logger.info(
                    f"⏳ Sticky model {model_id} at capacity on "
                    f"{capacity_gateway_url or 'UNKNOWN'} (reactive fallback path)"
                )
                if event_bus and has_busy_eviction_block:
                    await _emit_eviction_classification_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        trace=trace,
                        classification="busy_blocked",
                        failure_reason=(
                            "No idle models to evict; loaded models are currently busy"
                        ),
                    )
                raise_capacity_error(str(model_id), capacity_details)

        # Distinguish: model in catalog but infeasible vs truly absent
        model_in_any_catalog = any(
            model_id in fg.available_models for fg in federated_gateways
        )

        if event_bus:
            await _emit_routing_resource_gap_event(
                event_bus=event_bus,
                request_id=context.request_id,
                model_id=model_id,
                federated_gateways=federated_gateways,
            )

        if model_in_any_catalog:
            has_transient_eviction_block = any(
                any(
                    f.constraint == "eviction_blocked_by_busy_models"
                    for f in c.constraints_failed
                )
                for c in (trace.candidates if trace else [])
            )
            if has_transient_eviction_block and federated_manager:
                timeout_s = (routing_config or {}).get("eviction_wait_timeout_s", 300.0)
                selected_gateway, trace, waited_ms = await _wait_and_retry_selection(
                    federated_manager=federated_manager,
                    decision_engine=decision_engine,
                    placement=placement,
                    context=context,
                    event_bus=event_bus,
                    timeout_s=timeout_s,
                    stability_tracker=stability_tracker,
                )
                if selected_gateway is None:
                    raise_capacity_error(
                        str(model_id),
                        {
                            "reason": "eviction_queue_timeout",
                            "waited_ms": waited_ms,
                        },
                    )
            if selected_gateway is None:
                # Model known but temporarily unroutable — retryable 503
                constraint_summary = _build_constraint_summary(
                    trace,
                    federated_gateways,
                    context,
                )
                if event_bus:
                    await _emit_routing_model_infeasible_event(
                        event_bus=event_bus,
                        request_id=context.request_id,
                        model_id=model_id,
                        trace=trace,
                        excluded_gateway_ids=list(context.excluded_gateway_ids),
                    )
                raise_no_feasible_gateway_error(str(model_id), constraint_summary)

        # Genuinely absent from all gateway catalogs
        raise_model_unavailable_error(str(model_id))

    logger.info(
        f"📍 ROUTING (router-only): model={model_id} "
        f"gateway={selected_gateway.name} route_type=federated "
        f"tier={trace.selection_tier.name}"
    )

    try:
        # Execute eviction if needed (T2_FEASIBLE_EVICT tier)
        from systems.routing.selection.decision import FeasibilityTier

        from .eviction_execution import execute_master_eviction

        # Emit eviction hysteresis events (planner is sync; async caller emits)
        if event_bus and trace.candidates:
            gw_name = selected_gateway.name
            selected_candidate = next(
                (c for c in trace.candidates if c.gateway.name == gw_name),
                None,
            )
            plan = selected_candidate.eviction_plan if selected_candidate else None
            if plan and plan.cooldown_protected_count > 0:
                from src.scheduling.events.routing import EvictionCooldownApplied

                await event_bus.publish_async_nowait(
                    EvictionCooldownApplied(
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        protected_count=plan.cooldown_protected_count,
                        cooldown_s=eviction_cooldown_s,
                        timestamp=time.time(),
                    )
                )
            if plan and plan.demand_protected_count > 0:
                from src.scheduling.events.routing import EvictionDemandApplied

                from .routing_wait import count_demand_for

                waiter_counts = {}
                for c in trace.candidates:
                    if c.eviction_plan:
                        for m in c.eviction_plan.models_to_evict:
                            cnt = count_demand_for(m.routing_key)
                            if cnt > 0:
                                waiter_counts[m.routing_key] = cnt
                await event_bus.publish_async_nowait(
                    EvictionDemandApplied(
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        protected_count=plan.demand_protected_count,
                        waiter_counts=waiter_counts,
                        timestamp=time.time(),
                    )
                )
            if plan and plan.escape_hatch_used:
                from src.scheduling.events.routing import EvictionCooldownBlocked

                await event_bus.publish_async_nowait(
                    EvictionCooldownBlocked(
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        evicted_model_id=plan.escape_model_id or "",
                        escape_reason=plan.escape_reason or "unknown",
                        timestamp=time.time(),
                        request_id=context.request_id,
                        cooldown_remaining_s=plan.escape_cooldown_remaining_s,
                        candidates_in_cooldown=plan.cooldown_protected_count,
                        candidates_demand_protected=plan.demand_protected_count,
                    )
                )

        if trace.selection_tier == FeasibilityTier.T2_FEASIBLE_EVICT:
            eviction_ok = await execute_master_eviction(
                federation_forwarder=federation_forwarder,
                federated_manager=federated_manager,
                selected_gateway=selected_gateway,
                trace=trace,
                request_id=context.request_id,
                event_bus=event_bus,
            )
            if not eviction_ok:
                logger.warning(
                    f"⚠️ Eviction failed for {model_id} on {selected_gateway.name}"
                )
                raise_eviction_failed_error(
                    str(model_id),
                    selected_gateway.name,
                    gateway_url=selected_gateway.ref.remote_stargate_url,
                )

        # Load model on remote gateway
        if federated_load_orchestrator:
            try:
                await federated_load_orchestrator.ensure_model_loaded_on_remote(
                    selected_gateway.ref,  # FederatedGateway
                    model_id,
                    sticky=context.model_sticky,
                    request_id=context.request_id,
                )
            except HTTPException as e:
                detail = e.detail if isinstance(e.detail, dict) else {}
                # ∀ RESOURCE_UNAVAILABLE+retryable: transient VRAM pressure
                # (catalog drift caused DecisionEngine to skip eviction planning).
                # Enter the same
                # state-change wait as Phase 7 pre-selection queue. Once MODEL_UNLOADED
                # fires the wait resolves, re-selection finds VRAM free, retry succeeds.
                # ¬ mark_load_failed — that is for permanent incompatibilities only.
                if (
                    detail.get("code") == ErrorCode.RESOURCE_UNAVAILABLE
                    and detail.get("retryable", False)
                    and federated_manager is not None
                ):
                    timeout_s = (routing_config or {}).get(
                        "eviction_wait_timeout_s", 300.0
                    )
                    (
                        selected_gateway,
                        trace,
                        waited_ms,
                    ) = await _wait_and_retry_selection(
                        federated_manager=federated_manager,
                        decision_engine=decision_engine,
                        placement=placement,
                        context=context,
                        event_bus=event_bus,
                        timeout_s=timeout_s,
                        stability_tracker=stability_tracker,
                    )
                    if selected_gateway is None:
                        raise_capacity_error(
                            str(model_id),
                            {
                                "reason": "eviction_queue_timeout_post_load_fail",
                                "waited_ms": waited_ms,
                            },
                        )
                    assert selected_gateway is not None
                    # Re-attempt load on gateway selected after VRAM freed
                    try:
                        await federated_load_orchestrator.ensure_model_loaded_on_remote(
                            selected_gateway.ref,
                            model_id,
                            sticky=context.model_sticky,
                            request_id=context.request_id,
                        )
                    except Exception:
                        if federated_manager is not None:
                            federated_manager.mark_load_failed(
                                selected_gateway.ref.gateway_id, model_id
                            )
                        raise
                else:
                    if federated_manager is not None:
                        federated_manager.mark_load_failed(
                            selected_gateway.ref.gateway_id, model_id
                        )
                    raise
            except Exception:
                if federated_manager is not None:
                    federated_manager.mark_load_failed(
                        selected_gateway.ref.gateway_id, model_id
                    )
                raise
            logger.info(f"✅ Model {model_id} loaded on {selected_gateway.name}")
        else:
            logger.warning(
                f"No federated_load_orchestrator available, "
                f"assuming model {model_id} already loaded"
            )

        # Set context - is_federated and federated_gateway are computed properties
        context.selected_gateway = selected_gateway

        # Emit routing event
        if event_bus and context.selected_gateway:
            routing_time_ms = (time.time() - routing_start_time) * 1000
            try:
                from src.scheduling.events import RequestRouted

                # Gateway.ref is FederatedGateway with remote_stargate_url
                gateway_url = getattr(
                    context.selected_gateway.ref, "remote_stargate_url", "unknown"
                )

                was_queued = (
                    context.capacity_token is not None and context.capacity_token.queued
                )
                await event_bus.publish_async_nowait(
                    RequestRouted(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        gateway_url=gateway_url,
                        gateway_name=context.selected_gateway.name,
                        timestamp=time.time(),
                        routing_time_ms=routing_time_ms,
                        immediate_route=not was_queued,
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to emit REQUEST_ROUTED event: {e}")

        return (selected_gateway.name, None)

    except Exception:
        # Clear optimistic mark on any failure
        if (
            optimistic_mark_gateway_id
            and optimistic_mark_model_id
            and federated_manager
        ):
            federated_manager.clear_model_loading_optimistic(
                optimistic_mark_gateway_id, optimistic_mark_model_id
            )
        # Release capacity token on pre-forward failure (eviction, load, etc.)
        # Token release is idempotent — safe even if already released.
        if context.capacity_token:
            await context.capacity_token.release()
            context.capacity_token = None
        raise
