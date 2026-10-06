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
    raise_eviction_blocked_error,
)

if TYPE_CHECKING:
    from systems.federation.master.manager.federated_gateway_manager import (
        FederatedGatewayManager,
    )
    from systems.federation.master.routing.forward import FederatedRequestForwarder
    from systems.routing.capacity.pool import CapacityPool
    from systems.routing.selection.decision import DecisionEngine
    from systems.routing.selection.decision.protocols import RoutingKeyTracker
    from systems.routing.selection.decision.stability import StickyPlacementTracker
    from systems.routing.selection.types import Gateway, Placement, SelectionTrace

    from ...context import RequestContext

logger = get_logger(__name__)

_RETRYABLE_LOAD_CODES = frozenset(
    {
        ErrorCode.RESOURCE_UNAVAILABLE,
        ErrorCode.INSUFFICIENT_VRAM,
    }
)

# Sentinel: post-load path needs finalize-owned requeue (review B1).
_POST_LOAD_NEEDS_REQUEUE = object()


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
    capacity_pool: "CapacityPool | None" = None,
    routing_key_tracker: "RoutingKeyTracker | None" = None,
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
        from systems.routing.selection.decision.admission_verdict import (
            AdmissionVerdict,
        )
        from systems.routing.selection.decision.eviction_cooldown_policy import (
            CooldownOverrideKey,
        )

        from .eviction_execution import (
            MasterEvictionOutcome,
            execute_master_eviction,
            result_to_error_data,
        )
        from .eviction_requeue import requeue_after_transient_eviction

        selected_candidate = (
            next(
                (
                    candidate
                    for candidate in trace.candidates
                    if candidate.gateway.name == selected_gateway.name
                ),
                None,
            )
            if trace.candidates
            else None
        )
        plan = selected_candidate.eviction_plan if selected_candidate else None

        if event_bus and trace.candidates:
            if plan and plan.cooldown_protected_count > 0:
                from src.scheduling.events.routing import EvictionCooldownApplied

                await event_bus.publish_nowait(
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
                await event_bus.publish_nowait(
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

                await event_bus.publish_nowait(
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
            if (
                plan
                and plan.models_to_evict
                and capacity_pool is not None
            ):
                rc = routing_config or {}
                pause_duration_s = float(
                    rc.get(
                        "eviction_victim_pause_s",
                        rc.get("drain_duration_s", 30.0),
                    )
                )
                for evict_model in plan.models_to_evict:
                    capacity_pool.pause_admission(
                        evict_model.routing_key,
                        duration_s=pause_duration_s,
                        reason="eviction_execute_victim_pin",
                    )

            eviction_result = await execute_master_eviction(
                federation_forwarder=federation_forwarder,
                federated_manager=federated_manager,
                selected_gateway=selected_gateway,
                trace=trace,
                request_id=context.request_id,
                event_bus=event_bus,
                eviction_cooldown_s=eviction_cooldown_s,
            )
            if eviction_result.outcome == MasterEvictionOutcome.BLOCKED:
                can_requeue = (
                    federated_manager is not None
                    and eviction_result.verdict_class
                    == AdmissionVerdict.INSUFFICIENT_TRANSIENT.value
                    and eviction_result.reason == "cooldown_oscillation_breaker"
                )
                if not can_requeue:
                    raise_eviction_blocked_error(
                        str(model_id),
                        selected_gateway.name,
                        error_data=result_to_error_data(eviction_result),
                        gateway_url=selected_gateway.ref.remote_stargate_url,
                    )
                hold_key = None
                if eviction_result.victim_model_id:
                    hold_key = CooldownOverrideKey(
                        gateway_id=eviction_result.gateway_id
                        or selected_gateway.name,
                        victim_model_id=eviction_result.victim_model_id,
                    )
                if (
                    optimistic_mark_gateway_id
                    and optimistic_mark_model_id
                    and federated_manager
                ):
                    federated_manager.clear_model_loading_optimistic(
                        optimistic_mark_gateway_id, optimistic_mark_model_id
                    )
                    optimistic_mark_gateway_id = None
                    optimistic_mark_model_id = None
                    marked_loading = False
                return await requeue_after_transient_eviction(
                    context=context,
                    federated_manager=federated_manager,
                    federated_load_orchestrator=federated_load_orchestrator,
                    federation_forwarder=federation_forwarder,
                    routing_config=routing_config,
                    decision_engine=decision_engine,
                    placement=placement,
                    event_bus=event_bus,
                    stability_tracker=stability_tracker,
                    routing_start_time=routing_start_time,
                    eviction_cooldown_s=eviction_cooldown_s,
                    capacity_pool=capacity_pool,
                    routing_key_tracker=routing_key_tracker,
                    continuation_mode="cooldown_blocked",
                    timeout_reason="eviction_blocked_queue_timeout",
                    cooldown_hold_key=hold_key,
                    per_wake_cap_s=eviction_result.retry_after_s,
                )
            if eviction_result.outcome == MasterEvictionOutcome.EXECUTION_FAILED:
                if event_bus:
                    from src.scheduling.events.routing import (
                        RoutingEvictionExecuteFailed,
                    )

                    candidate_breakdown = [
                        {
                            "gateway_id": c.gateway.name,
                            "feasibility_tier": c.tier.name,
                            "constraints_failed": [
                                f.constraint for f in (c.constraints_failed or [])
                            ],
                        }
                        for c in (trace.candidates or [])
                    ]
                    await event_bus.publish_nowait(
                        RoutingEvictionExecuteFailed(
                            request_id=context.request_id,
                            model_id=str(model_id),
                            gateway_id=selected_gateway.name,
                            selection_tier=trace.selection_tier.name,
                            selection_reason=trace.selection_reason,
                            models_to_evict=(
                                [str(m) for m in plan.models_to_evict] if plan else []
                            ),
                            freed_vram_mb=plan.freed_vram_mb if plan else 0,
                            freed_ram_mb=plan.freed_ram_mb if plan else 0,
                            estimated_cost=plan.estimated_cost if plan else 0.0,
                            cooldown_protected_count=(
                                plan.cooldown_protected_count if plan else 0
                            ),
                            demand_protected_count=(
                                plan.demand_protected_count if plan else 0
                            ),
                            candidate_breakdown=candidate_breakdown,
                            timestamp=time.time(),
                        )
                    )

                if federated_manager is None:
                    raise_eviction_blocked_error(
                        str(model_id),
                        selected_gateway.name,
                        error_data=result_to_error_data(eviction_result),
                        gateway_url=selected_gateway.ref.remote_stargate_url,
                    )
                if (
                    optimistic_mark_gateway_id
                    and optimistic_mark_model_id
                ):
                    federated_manager.clear_model_loading_optimistic(
                        optimistic_mark_gateway_id, optimistic_mark_model_id
                    )
                    optimistic_mark_gateway_id = None
                    optimistic_mark_model_id = None
                    marked_loading = False
                return await requeue_after_transient_eviction(
                    context=context,
                    federated_manager=federated_manager,
                    federated_load_orchestrator=federated_load_orchestrator,
                    federation_forwarder=federation_forwarder,
                    routing_config=routing_config,
                    decision_engine=decision_engine,
                    placement=placement,
                    event_bus=event_bus,
                    stability_tracker=stability_tracker,
                    routing_start_time=routing_start_time,
                    eviction_cooldown_s=eviction_cooldown_s,
                    capacity_pool=capacity_pool,
                    routing_key_tracker=routing_key_tracker,
                    continuation_mode="execution_failure",
                    timeout_reason="eviction_execute_failure_queue_timeout",
                )

        if federated_load_orchestrator:
            load_outcome = await _ensure_remote_model_loaded(
                context=context,
                selected_gateway=selected_gateway,
                federated_manager=federated_manager,
                federated_load_orchestrator=federated_load_orchestrator,
            )
            if load_outcome is _POST_LOAD_NEEDS_REQUEUE:
                if (
                    optimistic_mark_gateway_id
                    and optimistic_mark_model_id
                    and federated_manager
                ):
                    federated_manager.clear_model_loading_optimistic(
                        optimistic_mark_gateway_id, optimistic_mark_model_id
                    )
                    optimistic_mark_gateway_id = None
                    optimistic_mark_model_id = None
                    marked_loading = False
                assert federated_manager is not None
                return await requeue_after_transient_eviction(
                    context=context,
                    federated_manager=federated_manager,
                    federated_load_orchestrator=federated_load_orchestrator,
                    federation_forwarder=federation_forwarder,
                    routing_config=routing_config,
                    decision_engine=decision_engine,
                    placement=placement,
                    event_bus=event_bus,
                    stability_tracker=stability_tracker,
                    routing_start_time=routing_start_time,
                    eviction_cooldown_s=eviction_cooldown_s,
                    capacity_pool=capacity_pool,
                    routing_key_tracker=routing_key_tracker,
                    continuation_mode="execution_failure",
                    timeout_reason="eviction_queue_timeout_post_load_fail",
                )

        context.selected_gateway = selected_gateway
        if event_bus:
            from src.scheduling.events import RequestGatewayTrace, RequestRouted

            gateway_url = getattr(
                context.selected_gateway.ref, "remote_stargate_url", "unknown"
            )
            was_queued = (
                context.capacity_token is not None and context.capacity_token.queued
            )
            capacity_gateway = (
                context.capacity_token.gateway_id if context.capacity_token else None
            )
            sticky_gateway = stability_tracker.get_current_best(model_id)
            gateway_values = [
                value
                for value in (
                    selected_gateway.name,
                    capacity_gateway,
                    sticky_gateway,
                    context.selected_gateway.name,
                )
                if value
            ]
            invariant_status = (
                "match"
                if gateway_values and len(set(gateway_values)) == 1
                else "mismatch"
                if len(gateway_values) > 1
                else "incomplete"
            )
            await event_bus.publish_nowait(
                RequestGatewayTrace(
                    request_id=context.request_id,
                    model_id=str(model_id),
                    phase="routed",
                    selected_gateway=selected_gateway.name,
                    capacity_gateway=capacity_gateway,
                    sticky_gateway=sticky_gateway,
                    final_gateway=context.selected_gateway.name,
                    forwarded_gateway=None,
                    remote_id=selected_gateway.ref.remote_stargate_id,
                    gateway_url=gateway_url,
                    invariant_status=invariant_status,
                    reason=f"selection_tier={trace.selection_tier.name}",
                )
            )
            await event_bus.publish_nowait(
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
) -> object | None:
    """Load remote model; return post-load requeue sentinel on retryable fail.

    Finalize owns mark release and the requeue call so peer marks are not wiped.
    """
    try:
        await federated_load_orchestrator.ensure_model_loaded_on_remote(
            selected_gateway.ref,
            context.selected_model,
            sticky=context.model_sticky,
            request_id=context.request_id,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = detail.get("code")
        if (
            code in _RETRYABLE_LOAD_CODES
            and detail.get("retryable", False)
            and federated_manager is not None
        ):
            return _POST_LOAD_NEEDS_REQUEUE
        raise
    return None
