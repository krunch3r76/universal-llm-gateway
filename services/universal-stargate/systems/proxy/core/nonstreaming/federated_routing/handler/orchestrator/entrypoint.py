"""
Primary federated routing entrypoint for non-streaming master requests.

The coordinator composes modular helpers for selection, queueing, admission,
rejection classification, and load/finalization side effects.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .admission import acquire_admission_token
from .load_and_finalize import finalize_selection_and_load
from .pre_route_queue import wait_for_retryable_capacity
from .rejection import handle_selection_rejection
from .selection import run_initial_selection

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.federation.master.circuit_breaker import FederationCircuitBreaker
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.orchestration.load_orchestrator import (
        FederatedLoadOrchestrator,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision.stability import StickyPlacementTracker

    from ...context import RequestContext

logger = get_logger(__name__)


async def _route_to_federated_gateway(
    context: "RequestContext",
    federated_manager: "FederatedGatewayManager | None",
    federated_load_orchestrator: "FederatedLoadOrchestrator | None",
    federation_forwarder: "FederatedRequestForwarder | None",
    event_bus: "EventBus | None",
    routing_start_time: float,
    routing_config: dict[str, Any] | None = None,
    stability_tracker: "StickyPlacementTracker | None" = None,
    routing_key_tracker=None,
    capacity_pool: "CapacityPool | None" = None,
    circuit_breaker: "FederationCircuitBreaker | None" = None,
) -> tuple[str | None, str | None]:
    """
    Route to a federated gateway and ensure target model is remotely available.

    The function enforces sticky invariants and queue semantics while preserving
    prior behavior for overflow, admission, and structured failure envelopes.
    """
    if stability_tracker is None:
        raise ValueError(
            "stability_tracker is required for routing stability. "
            "Ensure component_factory initializes StickyPlacementTracker."
        )
    if federated_manager is None:
        return None, None

    (
        selected_gateway,
        trace,
        gateways_for_routing,
        federated_gateways,
        decision_engine,
        placement,
        _policy,
        eviction_cooldown_s,
        allowed_gateway_ids_override,
        overflow_origin_gateway,
        overflow_depth_before,
    ) = await run_initial_selection(
        context=context,
        federated_manager=federated_manager,
        federated_load_orchestrator=federated_load_orchestrator,
        event_bus=event_bus,
        routing_config=routing_config,
        stability_tracker=stability_tracker,
        routing_key_tracker=routing_key_tracker,
        capacity_pool=capacity_pool,
        circuit_breaker=circuit_breaker,
    )

    selected_gateway, trace = await wait_for_retryable_capacity(
        selected_gateway=selected_gateway,
        trace=trace,
        event_bus=event_bus,
        decision_engine=decision_engine,
        gateways_for_routing=gateways_for_routing,
        placement=placement,
        context=context,
        stability_tracker=stability_tracker,
    )

    selected_gateway = await acquire_admission_token(
        context=context,
        selected_gateway=selected_gateway,
        gateways_for_routing=gateways_for_routing,
        routing_config=routing_config,
        event_bus=event_bus,
        capacity_pool=capacity_pool,
        stability_tracker=stability_tracker,
        allowed_gateway_ids_override=allowed_gateway_ids_override,
        overflow_origin_gateway=overflow_origin_gateway,
        overflow_depth_before=overflow_depth_before,
    )

    if selected_gateway is None:
        selected_gateway = await handle_selection_rejection(
            selected_gateway=selected_gateway,
            trace=trace,
            context=context,
            event_bus=event_bus,
            federated_manager=federated_manager,
            federated_gateways=federated_gateways,
            routing_config=routing_config,
            decision_engine=decision_engine,
            placement=placement,
            gateways_for_routing=gateways_for_routing,
            stability_tracker=stability_tracker,
        )
        # Post-rejection recovery guarantees non-null selection.
        assert selected_gateway is not None

    await _emit_orchestrator_decision(
        event_bus=event_bus,
        context=context,
        selected_gateway=selected_gateway,
        trace=trace,
        gateways_for_routing=gateways_for_routing,
    )

    selection_end_ms = int(time.time() * 1000)
    logger.info(
        "ROUTING END: %s selected %s at %sms (took %sms)",
        context.selected_model,
        selected_gateway.name,
        selection_end_ms,
        selection_end_ms - int(routing_start_time * 1000),
    )

    return await finalize_selection_and_load(
        context=context,
        selected_gateway=selected_gateway,
        trace=trace,
        event_bus=event_bus,
        federated_manager=federated_manager,
        federated_load_orchestrator=federated_load_orchestrator,
        federation_forwarder=federation_forwarder,
        routing_config=routing_config,
        decision_engine=decision_engine,
        placement=placement,
        stability_tracker=stability_tracker,
        routing_start_time=routing_start_time,
        eviction_cooldown_s=eviction_cooldown_s,
    )


async def _emit_orchestrator_decision(
    *,
    event_bus,
    context: "RequestContext",
    selected_gateway,
    trace,
    gateways_for_routing,
) -> None:
    """Emit the orchestrator decision event with bounded alternative context."""
    if event_bus is None:
        return

    from src.scheduling.events import FederationOrchestratorDecided

    decision_type = "route" if selected_gateway else "reject"
    target = selected_gateway.name if selected_gateway else None
    reason = (
        f"Selected {selected_gateway.name} (tier={trace.selection_tier.name})"
        if selected_gateway
        else "No feasible gateway available"
    )
    alternatives = [gateway.name for gateway in gateways_for_routing[:5]]

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
