"""
Rejection-path handling for federated orchestrator selection failures.

This module classifies transient capacity versus permanent infeasibility and
raises structured errors that preserve retry semantics for callers.

Orchestrates no-selection recovery, emits terminal side effects via combinator,
and raises the appropriate structured selection error. Supporting pure helpers
are in sibling modules within the rejection package.
"""

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from .....constraint_retryable import constraint_failure_is_retryable
from .....selection_errors import (
    raise_capacity_error,
    raise_model_unavailable_error,
    raise_no_feasible_gateway_error,
)
from ....errors import _build_constraint_summary
from ....events import (
    _emit_eviction_classification_event,
    _emit_routing_model_infeasible_event,
    _emit_routing_resource_gap_event,
)
from ....wait_continuation import clamp_eviction_wait_timeout
from ....wait_logic import _wait_and_retry_selection
from .capacity_details import _build_capacity_details
from .terminal_events import _emit_terminal_failure_events
from .topology_snapshot import _build_topology_snapshot

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.protocols import RoutingKeyTracker
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ....context import RequestContext


_TRANSIENT_CAPACITY_CONSTRAINTS: frozenset[str] = frozenset(
    {
        "compute_type_capacity",
        "circuit_breaker",
        "eviction_blocked_by_busy_models",
    }
)

_RESOURCE_CONSTRAINTS: frozenset[str] = frozenset({"has_enough_vram", "has_enough_ram"})

_ALL_CAPACITY_CONSTRAINTS: frozenset[str] = (
    _TRANSIENT_CAPACITY_CONSTRAINTS | _RESOURCE_CONSTRAINTS
)


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

    selected_model_key = context.selected_model.routing_key

    logger.error("No feasible federated gateway for %s", context.selected_model)

    if event_bus:
        from src.scheduling.events import FederationRoutingRejected

        await event_bus.publish_nowait(
            FederationRoutingRejected(
                request_id=context.request_id,
                model_id=selected_model_key,
                reason="No feasible gateway available",
            )
        )

    queue_timeout_info: dict[str, Any] | None = None
    if trace and trace.candidates:
        has_transient_capacity = any(
            any(
                failure.constraint in _TRANSIENT_CAPACITY_CONSTRAINTS
                for failure in candidate.constraints_failed
            )
            for candidate in trace.candidates
        )
        if has_transient_capacity:
            if event_bus:
                await _emit_eviction_classification_event(
                    event_bus=event_bus,
                    request_id=context.request_id,
                    model_id=context.selected_model,
                    trace=trace,
                    classification="transient_capacity",
                    failure_reason="Transient capacity contention; entering wait queue",
                )
            rc = routing_config or {}
            config_timeout = float(rc.get("eviction_wait_timeout_s", 300.0))
            starvation_drain_threshold_s = float(
                rc.get("starvation_drain_threshold_s", 15.0)
            )
            drain_duration_s = float(rc.get("drain_duration_s", 30.0))
            timeout_s = clamp_eviction_wait_timeout(context, config_timeout)
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
                continuation_mode="transient_capacity",
            )
            if selected_gateway is None:
                queue_timeout_info = {"waited_ms": waited_ms}

    if selected_gateway is not None:
        return selected_gateway

    if context.model_sticky and trace and trace.candidates:
        transient_constraints = _TRANSIENT_CAPACITY_CONSTRAINTS
        resource_constraints = _RESOURCE_CONSTRAINTS

        def _is_transient_capacity_failure(candidate) -> bool:
            failed = {failure.constraint for failure in candidate.constraints_failed}
            if failed & transient_constraints:
                return True
            if failed & resource_constraints:
                return "can_fit_with_eviction" not in failed
            return False

        def _is_permanent_resource_failure(candidate) -> bool:
            for failure in candidate.constraints_failed:
                if failure.constraint == "can_fit_with_eviction":
                    return not constraint_failure_is_retryable(failure)
            failed = {failure.constraint for failure in candidate.constraints_failed}
            return bool(failed & resource_constraints) and (
                "can_fit_with_eviction" in failed
            )

        has_capacity_failure = any(
            _is_transient_capacity_failure(candidate) for candidate in trace.candidates
        )
        has_permanent_resource_failure = any(
            _is_permanent_resource_failure(candidate) for candidate in trace.candidates
        )

        if has_permanent_resource_failure:
            from .....selection_errors import raise_insufficient_resources_error

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
                await _emit_terminal_failure_events(
                    event_bus=event_bus,
                    context=context,
                    trace=trace,
                    reason=failure_reason,
                )
            raise_insufficient_resources_error(selected_model_key, failure_reason)

        if has_capacity_failure:
            all_capacity_constraints = _ALL_CAPACITY_CONSTRAINTS
            capacity_details = _build_capacity_details(
                context.selected_model,
                trace,
                all_capacity_constraints,
                federated_gateways,
            )

            if queue_timeout_info:
                capacity_details.update(queue_timeout_info)

            await _emit_terminal_failure_events(
                event_bus=event_bus,
                context=context,
                trace=trace,
                reason="capacity_exhausted",
            )
            raise_capacity_error(selected_model_key, capacity_details)

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
            await _emit_terminal_failure_events(
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
            selected_model_key,
            constraint_summary,
            retryable=model_in_any_routing_candidate,
        )

    await _emit_terminal_failure_events(
        event_bus=event_bus,
        context=context,
        trace=trace,
        reason="model_unavailable",
    )
    topology_snapshot = _build_topology_snapshot(
        federated_manager, context.selected_model
    )
    raise_model_unavailable_error(
        selected_model_key,
        topology_snapshot=topology_snapshot,
    )
