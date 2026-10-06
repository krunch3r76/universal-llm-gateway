"""Shared master-side wait/requeue after transient eviction block or execute failure."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ....selection_errors import raise_capacity_error
from ...wait_continuation import ContinuationMode, clamp_eviction_wait_timeout
from ...wait_logic import _wait_and_retry_selection

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.eviction_cooldown_policy import (
        CooldownOverrideKey,
    )
    from systems.routing.selection.decision.protocols import RoutingKeyTracker
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Placement

    from ...context import RequestContext


async def requeue_after_transient_eviction(
    *,
    context: RequestContext,
    federated_manager: FederatedGatewayManager,
    federated_load_orchestrator: Any,
    federation_forwarder: FederatedRequestForwarder | None,
    routing_config: dict[str, Any] | None,
    decision_engine: DecisionEngine,
    placement: Placement,
    event_bus: Any,
    stability_tracker: StickyPlacementTracker,
    routing_start_time: float,
    eviction_cooldown_s: float,
    capacity_pool: CapacityPool | None,
    routing_key_tracker: RoutingKeyTracker | None,
    optimistic_mark_gateway_id: str | None,
    optimistic_mark_model_id: Any,
    continuation_mode: ContinuationMode,
    timeout_reason: str,
    cooldown_hold_key: CooldownOverrideKey | None = None,
    per_wake_cap_s: float | None = None,
) -> tuple[str, None]:
    """
    Release admission marks, wait for a fresh selection, then re-enter finalize.

    Used for both EXECUTION_FAILED and cooldown-breaker BLOCKED paths so the
    master absorbs transient eviction pressure instead of failing closed to
    the client before the capacity budget is exhausted.
    """
    from .load_and_finalize import finalize_selection_and_load

    if optimistic_mark_gateway_id and optimistic_mark_model_id:
        federated_manager.clear_model_loading_optimistic(
            optimistic_mark_gateway_id, optimistic_mark_model_id
        )

    if context.capacity_token:
        await context.capacity_token.release()
        context.capacity_token = None

    rc = routing_config or {}
    config_timeout = float(rc.get("eviction_wait_timeout_s", 300.0))
    timeout_s = clamp_eviction_wait_timeout(context, config_timeout)
    starvation_drain_threshold_s = float(
        rc.get("starvation_drain_threshold_s", 15.0)
    )
    drain_duration_s = float(rc.get("drain_duration_s", 30.0))

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
        continuation_mode=continuation_mode,
        cooldown_hold_key=cooldown_hold_key,
        per_wake_cap_s=per_wake_cap_s,
    )
    if selected_gateway is None:
        raise_capacity_error(
            str(context.selected_model),
            {"reason": timeout_reason, "waited_ms": waited_ms},
        )

    from systems.routing.selection.stargate_collector import (
        federated_gateways_to_routing_candidates,
    )

    from .admission import acquire_admission_token

    fresh_gateways = [
        g for g in federated_manager.get_all_gateways() if g.dispatchable
    ]
    gateways_for_routing = [
        g
        for g in federated_gateways_to_routing_candidates(fresh_gateways)
        if g.name not in (context.excluded_gateway_ids or set())
    ]
    selected_gateway = await acquire_admission_token(
        context=context,
        selected_gateway=selected_gateway,
        gateways_for_routing=gateways_for_routing,
        routing_config=routing_config,
        event_bus=event_bus,
        capacity_pool=capacity_pool,
        stability_tracker=stability_tracker,
        allowed_gateway_ids_override=None,
        overflow_origin_gateway=None,
        overflow_depth_before=0,
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
        capacity_pool=capacity_pool,
        routing_key_tracker=routing_key_tracker,
    )
