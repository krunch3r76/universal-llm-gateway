"""
Rejection-path handling for federated orchestrator selection failures.

This module classifies transient capacity versus permanent infeasibility and
raises structured errors that preserve retry semantics for callers.
"""

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from ....selection_errors import (
    raise_capacity_error,
    raise_model_unavailable_error,
    raise_no_feasible_gateway_error,
)
from ...errors import _build_constraint_summary
from ...events import (
    _emit_eviction_classification_event,
    _emit_routing_model_infeasible_event,
    _emit_routing_resource_gap_event,
)
from ...wait_logic import _wait_and_retry_selection

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)


async def handle_selection_rejection(
    *,
    selected_gateway: "Gateway | None",
    trace: "SelectionTrace | None",
    context: "RequestContext",
    event_bus,
    federated_manager: "FederatedGatewayManager",
    federated_gateways: list[Any],
    routing_config: dict[str, Any] | None,
    decision_engine: "DecisionEngine",
    placement: "Placement",
    gateways_for_routing: list["Gateway"],
    stability_tracker: "StickyPlacementTracker",
) -> "Gateway":
    """
    Resolve no-selection outcomes by waiting, classifying, or failing with context.

    Returns a selected gateway if recovery succeeds; otherwise raises one of the
    existing selection errors with detailed envelope data.
    """
    if selected_gateway is not None:
        return selected_gateway

    logger.error("No feasible federated gateway for %s", context.selected_model)

    if event_bus:
        from src.scheduling.events import FederationRoutingRejected

        await event_bus.publish_async_nowait(
            FederationRoutingRejected(
                request_id=context.request_id,
                model_id=str(context.selected_model),
                reason="No feasible gateway available",
            )
        )

    queue_timeout_info: dict[str, Any] | None = None
    if trace and trace.candidates:
        has_busy_block = any(
            any(
                failure.constraint == "eviction_blocked_by_busy_models"
                for failure in candidate.constraints_failed
            )
            for candidate in trace.candidates
        )
        if has_busy_block:
            if event_bus:
                await _emit_eviction_classification_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=context.selected_model,
                    trace=trace,
                    classification="busy_blocked",
                    failure_reason="No idle models to evict; entering wait queue",
                )
            timeout_s = (routing_config or {}).get("eviction_wait_timeout_s", 300.0)
            selected_gateway, trace, waited_ms = await _wait_and_retry_selection(
                federated_manager=federated_manager,
                decision_engine=decision_engine,
                placement=placement,
                context=context,
                event_bus=event_bus,
                timeout_s=float(timeout_s) if timeout_s is not None else 300.0,
                stability_tracker=stability_tracker,
            )
            if selected_gateway is None:
                queue_timeout_info = {"waited_ms": waited_ms}

    if selected_gateway is not None:
        return selected_gateway

    if context.model_sticky and trace and trace.candidates:
        transient_constraints: frozenset[str] = frozenset(
            {
                "compute_type_capacity",
                "circuit_breaker",
                "eviction_blocked_by_busy_models",
            }
        )
        resource_constraints: frozenset[str] = frozenset(
            {"has_enough_vram", "has_enough_ram"}
        )

        def _is_transient_capacity_failure(candidate) -> bool:
            failed = {failure.constraint for failure in candidate.constraints_failed}
            if failed & transient_constraints:
                return True
            if failed & resource_constraints:
                return "can_fit_with_eviction" in failed
            return False

        def _is_permanent_resource_failure(candidate) -> bool:
            failed = {failure.constraint for failure in candidate.constraints_failed}
            return "can_fit_with_eviction" in failed and bool(
                failed & resource_constraints
            )

        has_capacity_failure = any(
            _is_transient_capacity_failure(candidate) for candidate in trace.candidates
        )
        has_permanent_resource_failure = any(
            _is_permanent_resource_failure(candidate) for candidate in trace.candidates
        )

        if has_permanent_resource_failure and not has_capacity_failure:
            from ....selection_errors import raise_insufficient_resources_error

            failure_reason = next(
                (
                    failure.reason
                    for candidate in trace.candidates
                    for failure in candidate.constraints_failed
                    if failure.constraint in resource_constraints
                ),
                "VRAM/RAM insufficient to load model",
            )
            if event_bus:
                await _emit_eviction_classification_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=context.selected_model,
                    trace=trace,
                    classification="permanent_insufficient",
                    failure_reason=failure_reason,
                )
                await _emit_routing_model_infeasible_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=context.selected_model,
                    trace=trace,
                    excluded_gateway_ids=list(context.excluded_gateway_ids),
                )
            raise_insufficient_resources_error(
                str(context.selected_model), failure_reason
            )

        if has_capacity_failure:
            capacity_gateway_url = None
            capacity_details: dict[str, Any] = {"model_id": str(context.selected_model)}
            all_capacity_constraints = transient_constraints | resource_constraints

            for candidate in trace.candidates:
                for failure in candidate.constraints_failed:
                    if failure.constraint in all_capacity_constraints:
                        capacity_details.update(failure.details)
                        break

            for federated_gateway in federated_gateways:
                if context.selected_model in federated_gateway.loaded_models:
                    capacity_gateway_url = federated_gateway.remote_stargate_url
                    capacity_details["gateway_url"] = capacity_gateway_url
                    break

            if not capacity_gateway_url:
                for federated_gateway in federated_gateways:
                    if context.selected_model in federated_gateway.available_models:
                        capacity_gateway_url = federated_gateway.remote_stargate_url
                        capacity_details["gateway_url"] = capacity_gateway_url
                        break

            if queue_timeout_info:
                capacity_details.update(queue_timeout_info)

            raise_capacity_error(str(context.selected_model), capacity_details)

    model_in_any_catalog = any(
        context.selected_model in federated_gateway.available_models
        for federated_gateway in federated_gateways
    )

    if event_bus:
        await _emit_routing_resource_gap_event(
            event_bus=event_bus,
            request_id=context.request_id,
            model_id=context.selected_model,
            federated_gateways=federated_gateways,
        )

    if model_in_any_catalog:
        constraint_summary = _build_constraint_summary(
            trace, federated_gateways, context
        )
        if queue_timeout_info:
            constraint_summary.update(queue_timeout_info)
        if event_bus:
            await _emit_routing_model_infeasible_event(
                event_bus=event_bus,
                request_id=context.request_id,
                model_id=context.selected_model,
                trace=trace,
                excluded_gateway_ids=list(context.excluded_gateway_ids),
            )
        raise_no_feasible_gateway_error(str(context.selected_model), constraint_summary)

    raise_model_unavailable_error(str(context.selected_model))
