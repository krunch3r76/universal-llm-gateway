"""
Eviction, loading, and success-finalization helpers for orchestrator flow.

The helpers in this module run after selection and admission have already
decided the target gateway, keeping side effects in one testable boundary.
"""

import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from universal_logging import get_logger
from universal_protocol import ErrorCode

from ....selection_errors import (
    raise_capacity_error,
    raise_eviction_failed_error,
)
from ...wait_logic import _wait_and_retry_selection

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)


async def finalize_selection_and_load(
    *,
    context: "RequestContext",
    selected_gateway: "Gateway",
    trace: "SelectionTrace",
    event_bus,
    federated_manager: "FederatedGatewayManager | None",
    federated_load_orchestrator,
    federation_forwarder: "FederatedRequestForwarder | None",
    routing_config: dict[str, Any] | None,
    decision_engine: "DecisionEngine",
    placement: "Placement",
    stability_tracker: "StickyPlacementTracker",
    routing_start_time: float,
    eviction_cooldown_s: float,
) -> tuple[str, None]:
    """
    Execute eviction/load side effects and publish final routing event.

    Any failure releases optimistic loading marks and admission tokens before
    re-raising the original exception to preserve existing semantics.
    """
    model_id = context.selected_model
    marked_loading = False
    optimistic_mark_gateway_id = None
    optimistic_mark_model_id = None

    if federated_manager and model_id not in selected_gateway.loaded_models:
        marked_loading = federated_manager.mark_loading_optimistic(
            selected_gateway.ref.gateway_id, model_id
        )
        if marked_loading:
            optimistic_mark_gateway_id = selected_gateway.ref.gateway_id
            optimistic_mark_model_id = model_id

    try:
        from systems.routing.selection.decision import FeasibilityTier

        from ....eviction_execution import execute_master_eviction

        if event_bus and trace.candidates:
            selected_candidate = next(
                (
                    candidate
                    for candidate in trace.candidates
                    if candidate.gateway.name == selected_gateway.name
                ),
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

                from ....routing_wait import count_demand_for

                waiter_counts = {}
                for candidate in trace.candidates:
                    if candidate.eviction_plan:
                        for evict_model in candidate.eviction_plan.models_to_evict:
                            count = count_demand_for(evict_model.routing_key)
                            if count > 0:
                                waiter_counts[evict_model.routing_key] = count
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
                raise_eviction_failed_error(
                    str(model_id),
                    selected_gateway.name,
                    gateway_url=selected_gateway.ref.remote_stargate_url,
                )

        if federated_load_orchestrator:
            await _ensure_remote_model_loaded(
                context=context,
                selected_gateway=selected_gateway,
                federated_manager=federated_manager,
                federated_load_orchestrator=federated_load_orchestrator,
                routing_config=routing_config,
                decision_engine=decision_engine,
                placement=placement,
                event_bus=event_bus,
                stability_tracker=stability_tracker,
            )

        context.selected_gateway = selected_gateway
        if event_bus:
            from src.scheduling.events import RequestRouted

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
                    routing_time_ms=(time.time() - routing_start_time) * 1000,
                    immediate_route=not was_queued,
                )
            )
        return selected_gateway.name, None
    except Exception:
        if (
            optimistic_mark_gateway_id
            and optimistic_mark_model_id
            and federated_manager
        ):
            federated_manager.clear_model_loading_optimistic(
                optimistic_mark_gateway_id, optimistic_mark_model_id
            )
        if context.capacity_token:
            await context.capacity_token.release()
            context.capacity_token = None
        raise


async def _ensure_remote_model_loaded(
    *,
    context: "RequestContext",
    selected_gateway: "Gateway",
    federated_manager: "FederatedGatewayManager | None",
    federated_load_orchestrator,
    routing_config: dict[str, Any] | None,
    decision_engine: "DecisionEngine",
    placement: "Placement",
    event_bus,
    stability_tracker: "StickyPlacementTracker",
) -> None:
    """Load target model on remote gateway with retry path for transient load errors."""
    try:
        await federated_load_orchestrator.ensure_model_loaded_on_remote(
            selected_gateway.ref,
            context.selected_model,
            sticky=context.model_sticky,
            request_id=context.request_id,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if (
            detail.get("code") == ErrorCode.RESOURCE_UNAVAILABLE
            and detail.get("retryable", False)
            and federated_manager is not None
        ):
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
                    str(context.selected_model),
                    {
                        "reason": "eviction_queue_timeout_post_load_fail",
                        "waited_ms": waited_ms,
                    },
                )
            await federated_load_orchestrator.ensure_model_loaded_on_remote(
                selected_gateway.ref,
                context.selected_model,
                sticky=context.model_sticky,
                request_id=context.request_id,
            )
            return

        raise
