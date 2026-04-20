"""
Admission helpers for federated routing orchestrator.

This module owns pre-admission capacity bookkeeping and token acquisition so
selection logic remains focused on feasibility and scoring.
"""

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from ....selection_errors import raise_gateway_capacity_error
from ...events import _emit_overflow_assigned_event

if TYPE_CHECKING:
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway

    from ...context import RequestContext

logger = get_logger(__name__)


async def acquire_admission_token(
    *,
    context: "RequestContext",
    selected_gateway: "Gateway | None",
    gateways_for_routing: list["Gateway"],
    routing_config: dict | None,
    event_bus,
    capacity_pool,
    stability_tracker: "StickyPlacementTracker",
    allowed_gateway_ids_override: frozenset[str] | None,
    overflow_origin_gateway: str | None,
    overflow_depth_before: int,
) -> "Gateway | None":
    """
    Reserve request capacity and return the final assigned gateway.

    The helper may reassign the selected gateway if admission chooses another
    warm eligible target, preserving existing behavior.
    """
    if selected_gateway is None:
        return None

    model_id = context.selected_model
    is_cold_load = model_id not in selected_gateway.loaded_models

    if is_cold_load and capacity_pool:
        details = selected_gateway.model_details.get(model_id, {})
        catalog_capacity = int(details.get("max_concurrent_requests", 1))
        capacity_pool_config = (
            routing_config.get("capacity_pool", {})
            if isinstance(routing_config, dict)
            else {}
        )
        loading_phase_cap = int(capacity_pool_config.get("loading_phase_cap", 1))
        placeholder_capacity = min(catalog_capacity, loading_phase_cap)

        capacity_pool.set_capacity(
            selected_gateway.name,
            model_id.routing_key,
            placeholder_capacity,
        )
        logger.info(
            "Cold-load placeholder capacity: %s/%s -> %s slot(s) while loading "
            "(catalog=%s)",
            selected_gateway.name,
            model_id.routing_key,
            placeholder_capacity,
            catalog_capacity,
        )
        if event_bus:
            from src.scheduling.events import RoutingCapacityPreseeded

            asyncio.create_task(
                event_bus.publish_nowait(
                    RoutingCapacityPreseeded(
                        request_id=context.request_id,
                        model_id=str(model_id),
                        gateway_id=selected_gateway.name,
                        placeholder_capacity=placeholder_capacity,
                        catalog_capacity=catalog_capacity,
                    )
                )
            )

    if event_bus and capacity_pool and not is_cold_load:
        pool_available, pool_in_flight, pool_capacity = capacity_pool.get_slot_info(
            selected_gateway.name,
            model_id.routing_key,
        )
        is_busy_per_telemetry = model_id in selected_gateway.busy_models
        if is_busy_per_telemetry and pool_available > 0:
            from src.scheduling.events import RoutingCapacityDivergence

            asyncio.create_task(
                event_bus.publish_nowait(
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

    if context.model_sticky and is_cold_load:
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
                "Sticky guard redirected %s from cold-load on %s to %s (%s)",
                model_id,
                selected_gateway.name,
                already_warm.name,
                warm_state,
            )
            selected_gateway = already_warm
            is_cold_load = model_id not in selected_gateway.loaded_models
            stability_tracker.update_binding(model_id, selected_gateway.name)

    if capacity_pool is None:
        return selected_gateway

    if allowed_gateway_ids_override is not None:
        allowed_gateway_ids = allowed_gateway_ids_override
    elif context.model_sticky or is_cold_load:
        allowed_gateway_ids = frozenset({selected_gateway.name})
    else:
        allowed_gateway_ids = frozenset(
            g.name for g in gateways_for_routing if model_id in g.loaded_models
        )

    try:
        token = await capacity_pool.acquire_token(
            request_id=context.request_id,
            model_id=model_id.routing_key,
            allowed_gateway_ids=allowed_gateway_ids,
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

        if token.gateway_id != selected_gateway.name:
            original_gateway_name = selected_gateway.name
            selected_gateway = next(
                (g for g in gateways_for_routing if g.name == token.gateway_id),
                selected_gateway,
            )
            logger.info(
                "Admission reassigned %s from %s to %s",
                model_id,
                original_gateway_name,
                token.gateway_id,
            )
    except Exception as exc:
        from systems.routing.capacity.pool import QueueFullError

        if isinstance(exc, QueueFullError):
            logger.warning(
                "Admission queue full: model=%s depth=%d max=%d — "
                "rejecting request %s immediately",
                model_id.routing_key,
                exc.current_depth,
                exc.max_depth,
                context.request_id,
            )
            raise_gateway_capacity_error(selected_gateway.name)
        logger.error(
            "Admission queue acquire failed for model=%s gateway=%s request_id=%s: %s",
            model_id.routing_key,
            selected_gateway.name,
            context.request_id,
            exc,
        )
        stability_tracker.clear_binding(model_id)
        raise_gateway_capacity_error(selected_gateway.name)

    return selected_gateway
