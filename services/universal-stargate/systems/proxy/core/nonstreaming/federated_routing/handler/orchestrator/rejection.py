"""
Rejection-path handling for federated orchestrator selection failures.

This module classifies transient capacity versus permanent infeasibility and
raises structured errors that preserve retry semantics for callers.
"""

import time
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
    _emit_overflow_failed_event,
    _emit_routing_model_infeasible_event,
    _emit_routing_resource_gap_event,
)
from ...wait_logic import _wait_and_retry_selection

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.protocols import RoutingKeyTracker
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)


async def _emit_terminal_overflow_failure_if_needed(
    *,
    event_bus,
    context: "RequestContext",
) -> None:
    """Emit overflow failure only if the request later dies in terminal rejection."""
    if event_bus is None:
        return

    tried_gateways = context._overflow_failed_tried_gateways
    reason = context._overflow_failed_reason
    if not tried_gateways or reason is None:
        return

    await _emit_overflow_failed_event(
        event_bus=event_bus,
        request_id=context.request_id,
        model_id=context.selected_model,
        tried_gateways=tried_gateways,
        reason=reason,
    )


async def _emit_terminal_routing_failure(
    *,
    event_bus,
    context: "RequestContext",
    trace: "SelectionTrace | None",
    reason: str,
) -> None:
    """Emit scheduler.routing.failed only for terminal rejection outcomes."""
    if event_bus is None:
        return

    from src.scheduling.events import RoutingDecisionFailed

    candidate_count = len(trace.candidates) if trace else 0
    evaluation_time_ms = trace.evaluation_time_ms if trace else 0.0
    original_model_id = trace.original_model_id if trace else None
    timestamp = time.time()

    await event_bus.publish_async_nowait(
        RoutingDecisionFailed(
            model_id=str(context.selected_model),
            candidate_count=candidate_count,
            evaluation_time_ms=evaluation_time_ms,
            timestamp=timestamp,
            reason=reason,
            original_model_id=original_model_id,
            request_id=context.request_id,
        )
    )


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
    capacity_pool: "CapacityPool | None" = None,
    routing_key_tracker: "RoutingKeyTracker | None" = None,
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
            rc = routing_config or {}
            config_timeout = float(rc.get("eviction_wait_timeout_s", 300.0))
            starvation_drain_threshold_s = float(
                rc.get("starvation_drain_threshold_s", 15.0)
            )
            drain_duration_s = float(rc.get("drain_duration_s", 30.0))
            deadline = getattr(context, "_capacity_deadline_mono", None)
            if deadline is not None:
                timeout_s = min(config_timeout, max(0.0, deadline - time.monotonic()))
            else:
                timeout_s = config_timeout
            selected_gateway, trace, waited_ms = await _wait_and_retry_selection(
                federated_manager=federated_manager,
                decision_engine=decision_engine,
                placement=placement,
                context=context,
                event_bus=event_bus,
                timeout_s=timeout_s,
                stability_tracker=stability_tracker,
                capacity_pool=capacity_pool,
                routing_key_tracker=routing_key_tracker,
                starvation_drain_threshold_s=starvation_drain_threshold_s,
                drain_duration_s=drain_duration_s,
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
            return bool(failed & resource_constraints) and (
                "can_fit_with_eviction" not in failed
            )

        has_capacity_failure = any(
            _is_transient_capacity_failure(candidate) for candidate in trace.candidates
        )
        has_permanent_resource_failure = any(
            _is_permanent_resource_failure(candidate) for candidate in trace.candidates
        )

        if has_permanent_resource_failure:
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
                await _emit_terminal_overflow_failure_if_needed(
                    event_bus=event_bus,
                    context=context,
                )
                await _emit_terminal_routing_failure(
                    event_bus=event_bus,
                    context=context,
                    trace=trace,
                    reason=failure_reason,
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

            await _emit_terminal_overflow_failure_if_needed(
                event_bus=event_bus,
                context=context,
            )
            await _emit_terminal_routing_failure(
                event_bus=event_bus,
                context=context,
                trace=trace,
                reason="capacity_exhausted",
            )
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
            await _emit_terminal_overflow_failure_if_needed(
                event_bus=event_bus,
                context=context,
            )
            await _emit_terminal_routing_failure(
                event_bus=event_bus,
                context=context,
                trace=trace,
                reason="no_feasible_gateways",
            )

        # Model exists in *some* catalog but may not be in any active routing
        # candidate.  When every candidate fails has_model_available, retrying
        # cannot make progress — the presence is stale federation state only.
        model_in_any_routing_candidate = any(
            context.selected_model in gw.available_models for gw in gateways_for_routing
        )
        raise_no_feasible_gateway_error(
            str(context.selected_model),
            constraint_summary,
            retryable=model_in_any_routing_candidate,
        )

    await _emit_terminal_overflow_failure_if_needed(
        event_bus=event_bus,
        context=context,
    )
    await _emit_terminal_routing_failure(
        event_bus=event_bus,
        context=context,
        trace=trace,
        reason="model_unavailable",
    )
    raise_model_unavailable_error(str(context.selected_model))
