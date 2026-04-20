"""
Selection-phase helpers for federated routing orchestration.

This module resolves endpoint category, gathers healthy candidates, runs the
decision engine, and optionally applies non-sticky overflow pre-selection.
"""

import asyncio
import time
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from systems.federation.common.config.schema import EndpointCategory

from .....endpoint_category import derive_endpoint_category
from ....selection_errors import (
    raise_all_gateways_excluded_error,
    raise_inference_banned_error,
    raise_no_gateways_error,
)
from ...wait_logic import (
    DEFAULT_MODEL_GATEWAY_GRACE_TIMEOUT_S,
    DEFAULT_STARTUP_QUEUE_TIMEOUT_S,
    wait_for_model_gateway,
    wait_for_startup_gateway,
)
from .overflow import apply_non_sticky_overflow

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from systems.federation.master.circuit_breaker import FederationCircuitBreaker
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.orchestration.load_orchestrator import (
        FederatedLoadOrchestrator,
    )
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.config import RoutingPolicy
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)


def resolve_endpoint_category(context: "RequestContext") -> EndpointCategory:
    """
    Resolve endpoint category from request context with explicit fallback behavior.

    The selected category is persisted on context so subsequent forwarding and
    reservation logic observe the same classification.
    """
    if context.routing_endpoint_category is not None:
        logger.debug(
            "Using pre-set endpoint category: %s", context.routing_endpoint_category
        )
        return context.routing_endpoint_category

    try:
        endpoint_category = derive_endpoint_category(request=context.http_request)
    except ValueError:
        logger.error(
            "Could not derive endpoint category from request; "
            "defaulting to generation for continuity"
        )
        endpoint_category = EndpointCategory.GENERATION

    context.routing_endpoint_category = endpoint_category
    return endpoint_category


async def run_initial_selection(
    *,
    context: "RequestContext",
    federated_manager: "FederatedGatewayManager",
    federated_load_orchestrator: "FederatedLoadOrchestrator | None",
    event_bus: "EventBus | None",
    routing_config: dict[str, Any] | None,
    stability_tracker: "StickyPlacementTracker",
    routing_key_tracker,
    capacity_pool: "CapacityPool | None",
    circuit_breaker: "FederationCircuitBreaker | None",
) -> tuple[
    "Gateway | None",
    "SelectionTrace | None",
    list["Gateway"],
    list[Any],
    "DecisionEngine",
    "Placement",
    "RoutingPolicy",
    float,
    frozenset[str] | None,
    str | None,
    int,
]:
    """
    Build decision context and produce first-pass selection plus overflow metadata.

    Parameters:
        context: Request context (model ID, request ID, etc.).
        federated_manager: Manages federated gateways and health.
        federated_load_orchestrator: Orchestrates load across gateways.
        event_bus: Optional event bus for routing events.
        routing_config: Routing policy configuration.
        stability_tracker: Tracks sticky placements.
        routing_key_tracker: Tracks routing keys for the decision engine.
        capacity_pool: Optional capacity pool.
        circuit_breaker: Optional federation circuit breaker.

    Returns the selected gateway, decision trace, and all state required for
    downstream admission and rejection handling.
    """
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.config import load_routing_policy
    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )
    from systems.routing.selection.types import Placement

    from ....routing_wait import has_demand_for

    model_id = context.selected_model
    endpoint_category = resolve_endpoint_category(context)

    logger.info("ROUTING START: %s at %sms", model_id, int(time.time() * 1000))

    all_gateways = federated_manager.get_all_gateways()
    logger.info("Router-only total gateways registered: %s", len(all_gateways))
    for gateway in all_gateways:
        logger.debug(
            "Gateway %s age=%sms unreachable=%s",
            gateway.gateway_id,
            gateway.telemetry_age_ms,
            gateway.is_unreachable,
        )

    federated_gateways = federated_manager.get_healthy_gateways()
    logger.info("Router-only healthy gateways: %s", len(federated_gateways))

    if len(all_gateways) > len(federated_gateways) and event_bus:
        healthy_gateway_ids = {g.gateway_id for g in federated_gateways}
        dropped_gateways = [
            g for g in all_gateways if g.gateway_id not in healthy_gateway_ids
        ]
        if dropped_gateways:
            from src.scheduling.events import RoutingDebugGatewayDropout

            asyncio.create_task(
                event_bus.publish_nowait(
                    RoutingDebugGatewayDropout(
                        model_id=str(model_id),
                        stage="health_filter",
                        all_gateway_ids=[g.gateway_id for g in all_gateways],
                        surviving_gateway_ids=list(healthy_gateway_ids),
                        dropped_gateway_ids=[g.gateway_id for g in dropped_gateways],
                        detail={
                            g.gateway_id: {
                                "hb_age_ms": g.heartbeat_age_ms,
                                "telem_age_ms": g.telemetry_age_ms,
                                "unreachable": g.is_unreachable,
                                "catalog_size": len(g.available_models),
                            }
                            for g in dropped_gateways
                        },
                    )
                )
            )

    if not federated_gateways:
        startup_timeout_s = float(
            (routing_config or {})
            .get("request_queue", {})
            .get("startup_queue_timeout_s", DEFAULT_STARTUP_QUEUE_TIMEOUT_S)
        )
        remaining = startup_timeout_s - federated_manager.uptime_s
        if remaining > 0:
            await wait_for_startup_gateway(
                federated_manager=federated_manager,
                context=context,
                event_bus=event_bus,
                timeout_s=remaining,
            )
            federated_gateways = federated_manager.get_healthy_gateways()

        if not federated_gateways:
            raise_no_gateways_error()

    model_on_any_healthy = any(
        model_id in g.available_models for g in federated_gateways
    )
    if not model_on_any_healthy:
        unhealthy_with_model = [
            g.gateway_id
            for g in all_gateways
            if model_id in g.available_models
            and g.gateway_id
            not in {h.gateway_id for h in federated_gateways}
        ]
        if unhealthy_with_model:
            grace_timeout_s = float(
                (routing_config or {})
                .get("request_queue", {})
                .get(
                    "model_gateway_grace_timeout_s",
                    DEFAULT_MODEL_GATEWAY_GRACE_TIMEOUT_S,
                )
            )
            recovered = await wait_for_model_gateway(
                federated_manager=federated_manager,
                context=context,
                event_bus=event_bus,
                model_id=str(model_id),
                timeout_s=grace_timeout_s,
                unhealthy_gateway_ids=unhealthy_with_model,
            )
            if recovered:
                federated_gateways = federated_manager.get_healthy_gateways()

    gateways_for_routing = federated_gateways_to_routing_candidates(federated_gateways)

    if context.excluded_gateway_ids:
        kept = [
            gateway
            for gateway in gateways_for_routing
            if gateway.name not in context.excluded_gateway_ids
        ]
        has_model_alternative = any(
            model_id in gateway.available_models or model_id in gateway.loaded_models
            for gateway in kept
        )
        if not kept or not has_model_alternative:
            logger.warning(
                "All gateways for %s excluded after upstream failures: %s",
                model_id,
                context.excluded_gateway_ids,
            )
            if event_bus:
                from src.scheduling.events import RoutingUpstreamAllExcluded

                asyncio.create_task(
                    event_bus.publish_nowait(
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
        gateways_for_routing = kept

    inference_banned_ids = [
        gateway.name
        for gateway in gateways_for_routing
        if federated_manager.is_inference_banned(gateway.name, model_id)
    ]
    if inference_banned_ids:
        banned_set = set(inference_banned_ids)
        eligible = [
            gateway
            for gateway in gateways_for_routing
            if gateway.name not in banned_set
        ]
        if not eligible:
            raise_inference_banned_error(str(model_id), sorted(inference_banned_ids))
        gateways_for_routing = eligible

    vram_mb = 0
    ram_mb = 0
    for federated_gateway in federated_gateways:
        if model_id in federated_gateway.model_resources:
            resources = federated_gateway.model_resources[model_id]
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

    policy = load_routing_policy(routing_config or {})
    is_gateway_available_fn = (
        circuit_breaker.is_request_allowed_sync if circuit_breaker else None
    )
    eviction_cooldown_s = float(
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

    selected_gateway, trace = decision_engine.select(
        gateways=gateways_for_routing,
        placement=placement,
        request_id=context.request_id,
        sticky=context.model_sticky,
        stability_tracker=stability_tracker,
    )

    (
        selected_gateway,
        trace,
        allowed_gateway_ids_override,
        overflow_origin_gateway,
        overflow_depth_before,
    ) = await apply_non_sticky_overflow(
        selected_gateway=selected_gateway,
        trace=trace,
        context=context,
        policy=policy,
        capacity_pool=capacity_pool,
        event_bus=event_bus,
        decision_engine=decision_engine,
        gateways_for_routing=gateways_for_routing,
        placement=placement,
        federated_load_orchestrator=federated_load_orchestrator,
        stability_tracker=stability_tracker,
    )

    return (
        selected_gateway,
        trace,
        gateways_for_routing,
        federated_gateways,
        decision_engine,
        placement,
        policy,
        eviction_cooldown_s,
        allowed_gateway_ids_override,
        overflow_origin_gateway,
        overflow_depth_before,
    )
