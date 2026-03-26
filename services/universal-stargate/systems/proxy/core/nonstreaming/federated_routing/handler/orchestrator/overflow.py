"""
Overflow selection helpers for non-sticky federated routing.

These helpers isolate the second-pass spillover branch so the primary
selection logic remains linear and easier to verify.
"""

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ...events import (
    _emit_overflow_load_started_event,
    _emit_overflow_triggered_event,
)

if TYPE_CHECKING:
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ....routing_wait import RoutingPolicy
    from ...context import RequestContext

logger = get_logger(__name__)


async def apply_non_sticky_overflow(
    *,
    selected_gateway: "Gateway | None",
    trace: "SelectionTrace | None",
    context: "RequestContext",
    policy: "RoutingPolicy",
    capacity_pool,
    event_bus,
    decision_engine: "DecisionEngine",
    gateways_for_routing: list["Gateway"],
    placement: "Placement",
    federated_load_orchestrator,
    stability_tracker: "StickyPlacementTracker",
) -> tuple[
    "Gateway | None", "SelectionTrace | None", frozenset[str] | None, str | None, int
]:
    """
    Execute optional non-sticky overflow selection when the primary gateway saturates.

    The helper returns updated selection state plus admission override metadata
    consumed later by the token acquisition phase.
    """
    allowed_gateway_ids_override: frozenset[str] | None = None
    overflow_origin_gateway: str | None = None
    overflow_depth_before = 0

    if (
        selected_gateway is None
        or context.model_sticky
        or not policy.non_sticky_overflow_enabled
        or capacity_pool is None
    ):
        return (
            selected_gateway,
            trace,
            allowed_gateway_ids_override,
            overflow_origin_gateway,
            overflow_depth_before,
        )

    primary_available, primary_in_flight, primary_capacity = (
        capacity_pool.get_slot_info(
            selected_gateway.name, context.selected_model.routing_key
        )
    )
    queue_pressure = max(0, primary_in_flight - primary_capacity)
    primary_saturated = primary_capacity > 0 and primary_available <= 0
    queue_over_threshold = queue_pressure >= policy.non_sticky_overflow_queue_threshold

    if not (primary_saturated or queue_over_threshold):
        return (
            selected_gateway,
            trace,
            allowed_gateway_ids_override,
            overflow_origin_gateway,
            overflow_depth_before,
        )

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
        context._overflow_failed_tried_gateways = [
            g.name for g in gateways_for_routing if g.name != selected_gateway.name
        ]
        context._overflow_failed_reason = (
            overflow_trace.selection_reason
            if overflow_trace
            else "no_alternate_gateway"
        )
        return (
            selected_gateway,
            trace,
            allowed_gateway_ids_override,
            overflow_origin_gateway,
            overflow_depth_before,
        )

    if event_bus:
        await _emit_overflow_triggered_event(
            event_bus=event_bus,
            request_id=context.request_id,
            model_id=context.selected_model,
            from_gateway=selected_gateway.name,
            to_gateway=overflow_gateway.name,
            reason=(
                "queue_threshold_exceeded"
                if queue_over_threshold
                else "primary_capacity_saturated"
            ),
        )

    original_selected_gateway = selected_gateway
    original_trace = trace
    try:
        if (
            federated_load_orchestrator
            and context.selected_model not in overflow_gateway.loaded_models
            and context.selected_model not in overflow_gateway.loading_models
        ):
            if event_bus:
                await _emit_overflow_load_started_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=context.selected_model,
                    gateway_id=overflow_gateway.name,
                    reason="overflow_spillover",
                )
            await federated_load_orchestrator.ensure_model_loaded_on_remote(
                overflow_gateway.ref,
                context.selected_model,
                sticky=False,
                request_id=context.request_id,
            )

        selected_gateway = overflow_gateway
        trace = overflow_trace
        allowed_gateway_ids_override = frozenset(
            g.name
            for g in gateways_for_routing
            if context.selected_model in g.loaded_models
        ) | frozenset({overflow_gateway.name})
    except Exception as exc:
        logger.warning(
            "Overflow load attempt failed for %s on %s: %s",
            context.selected_model,
            overflow_gateway.name,
            exc,
        )
        # NOTE: This unobserved live branch is retained only so a terminal
        # rejection can still emit routing.overflow.failed(reason=overflow_load_failed)
        # if it ever occurs; see agent-bus thread 169. Remove as dead code once
        # overflow_load_failed is retired from the event contract.
        context._overflow_failed_tried_gateways = [overflow_gateway.name]
        context._overflow_failed_reason = "overflow_load_failed"
        selected_gateway = original_selected_gateway
        trace = original_trace

    return (
        selected_gateway,
        trace,
        allowed_gateway_ids_override,
        overflow_origin_gateway,
        overflow_depth_before,
    )
