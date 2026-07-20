"""
Feasibility evaluation: classify gateways into T0 / T1 / T2 tiers.

Runs early gates, resource checks, and eviction planning. Concurrency slots
are CapacityPool admission — not encoded in these tiers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .admission_verdict import AdmissionVerdict
from .eviction_cooldown_policy import (
    DEFAULT_EVICTION_COOLDOWN_S,
    EvictionRequestClass,
)
from .eviction_planning import _compute_eviction_plan
from .feasibility_gates import early_feasibility_gates
from .feasibility_reclaim import _can_fit_after_eviction_including_busy
from .resource_checks import (
    _calculate_required_resources,
    _check_resources,
    resolve_gateway_requirements,
)
from .types import ConstraintFailure, EvictionPlanSummary, FeasibilityTier

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .config import RoutingPolicy
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)

_COLD_LOAD_MIN_SLACK_MB = 1024


def evaluate_feasibility(
    gateway: Gateway,
    placement: Placement,
    policy: RoutingPolicy,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    sticky: bool = True,
    routing_key_tracker: RoutingKeyTracker | None = None,
    is_gateway_available_fn: Callable[[str, str], bool] | None = None,
    eviction_cooldown_s: float = DEFAULT_EVICTION_COOLDOWN_S,
    has_demand: Callable[[str], bool] | None = None,
    eviction_request_class: EvictionRequestClass = EvictionRequestClass.REQUIRED,
) -> tuple[
    FeasibilityTier,
    tuple[ConstraintFailure, ...],
    EvictionPlanSummary | None,
]:
    """
    Evaluate gateway feasibility for model placement.

    Returns (tier, constraint_failures, eviction_plan).

    Admission control: CapacityPool in systems/routing/capacity/

    Invariant: tier == T0 ⟹ len(constraint_failures) > 0
    Invariant: tier == T2 ⟹ eviction_plan is not None
    Invariant (non-sticky): ¬sticky ⟹
               T2_FEASIBLE_EVICT valid even when model loaded elsewhere (i.e.,
               routing can move the model, and eviction on this gateway is still
               a valid path).

    Tier classification:
    - T0: Unhealthy, model not in catalog, cannot fit
    - T1: Model already loaded OR fits with current free resources
    - T2: Can fit after eviction of idle models

    Args:
        gateway: Gateway to evaluate
        placement: Model placement requirements
        policy: Routing policy with capacity config
        requirements_lookup: MANDATORY function to look up (vram_mb, ram_mb)
                           for loading models (in-memory, no I/O)
        sticky: Whether this is sticky routing (affects capacity handling)
        routing_key_tracker: For eviction protection and stale busy-model
            reconciliation in eviction planning.
        eviction_cooldown_s: Minimum seconds since load before a model is evictable.
        has_demand: Callback returning True when routing queue has waiters for a
            routing_key.
        eviction_request_class: Required evictions may override cooldown above the
            hard floor; opportunistic evictions honor cooldown as an absolute veto.
    """
    logger.info(
        f"🔍 Evaluating feasibility: {placement.model_id} on {gateway.name} "
        f"(VRAM: {gateway.vram_free_mb}/{gateway.vram_total_mb}MB, "
        f"active_requests: {gateway.active_requests}, sticky={sticky})"
    )
    failures: list[ConstraintFailure] = []

    gate_result = early_feasibility_gates(
        gateway,
        placement,
        sticky=sticky,
        is_gateway_available_fn=is_gateway_available_fn,
    )
    if gate_result is not None:
        return gate_result

    # Check 4: Sufficient resources without eviction
    logger.debug(
        f"🔍 FEASIBILITY Check 4 (resources): Checking if {placement.model_id} "
        f"fits on {gateway.name} without eviction"
    )

    has_resources, resource_failure = _check_resources(
        gateway,
        placement,
        requirements_lookup,
        config={"resource_margins": policy.resource_margins},
    )

    if has_resources:
        resolved = resolve_gateway_requirements(gateway, placement)
        if (
            not isinstance(resolved, ConstraintFailure)
            and gateway.loaded_models
            and placement.model_id not in gateway.loaded_models
        ):
            gw_vram_mb, gw_ram_mb = resolved
            vram_needed, _, _, _, _ = _calculate_required_resources(
                vram_mb=gw_vram_mb,
                ram_mb=gw_ram_mb,
                resource_margins=policy.resource_margins,
            )
            vram_slack_mb = gateway.vram_free_mb - vram_needed
            if gw_vram_mb > 0 and 0 <= vram_slack_mb < _COLD_LOAD_MIN_SLACK_MB:
                eviction_plan = _compute_eviction_plan(
                    gateway,
                    placement,
                    requirements_lookup,
                    routing_key_tracker=routing_key_tracker,
                    eviction_cooldown_s=eviction_cooldown_s,
                    has_demand=has_demand,
                    resource_margins=policy.resource_margins,
                    eviction_request_class=eviction_request_class,
                )
                if eviction_plan is not None:
                    logger.info(
                        f"✅ FEASIBILITY T2 (low cold-load slack): "
                        f"{placement.model_id} on {gateway.name} has only "
                        f"{vram_slack_mb}MB VRAM slack; evicting idle model(s) "
                        f"instead of attempting knife-edge T1 load"
                    )
                    return FeasibilityTier.T2_FEASIBLE_EVICT, (), eviction_plan
        logger.info(
            f"✅ FEASIBILITY T1 (no eviction): {placement.model_id} on {gateway.name} "
            f"has sufficient resources without eviction"
        )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    # Catalog integrity violation cannot be resolved by eviction — short-circuit
    if (
        resource_failure
        and resource_failure.constraint == "missing_gateway_resource_data"
    ):
        failures.append(resource_failure)
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    logger.debug(
        f"🔍 FEASIBILITY Check 4 (eviction): {placement.model_id} on {gateway.name} "
        f"needs eviction (resource check failed)"
    )

    # Check 5: Can eviction provide enough resources?
    eviction_plan = _compute_eviction_plan(
        gateway,
        placement,
        requirements_lookup,
        routing_key_tracker=routing_key_tracker,
        eviction_cooldown_s=eviction_cooldown_s,
        has_demand=has_demand,
        resource_margins=policy.resource_margins,
        eviction_request_class=eviction_request_class,
    )

    if eviction_plan is None:
        # If resource_failure exists, it's the primary reason for not fitting
        # without eviction.
        # We should ensure it's included, but avoid duplicating it if it's
        # already implicitly covered by the more specific eviction-related failures.
        # For now, append it if it's distinct and relevant.
        if resource_failure is not None and resource_failure not in failures:
            failures.append(resource_failure)

        # Distinguish transient from permanent eviction failure.
        #
        # Transient: reclaimable resources (free + evictable loaded usage) can
        # satisfy the requirement with margins once loaded models can be evicted.
        # Queueing/retry is useful.
        #
        # Permanent: even reclaiming all currently loaded model usage cannot
        # satisfy required resources with margins; retrying will loop.
        #
        # ∀ loaded ∈ gateway.loaded_models: eventually idle → evictable
        can_fit_theoretically, reclaimable = _can_fit_after_eviction_including_busy(
            gateway,
            placement,
            requirements_lookup,
            policy.resource_margins,
        )
        verdict_class = reclaimable.get("verdict_class")
        if can_fit_theoretically:
            failures.append(
                ConstraintFailure(
                    constraint="eviction_blocked_by_busy_models",
                    reason=(
                        "No idle evictable models right now; reclaimable resources "
                        "indicate model can fit after queued requests complete."
                    ),
                    details={
                        "vram_free": gateway.vram_free_mb,
                        "vram_total": gateway.vram_total_mb,
                        "ram_free": gateway.ram_free_mb,
                        "ram_total": gateway.ram_total_mb,
                        "loaded_count": len(gateway.loaded_models),
                        "busy_count": len(gateway.busy_models),
                        "retryable": True,
                        "classification_basis": "reclaimable_resources",
                        **reclaimable,
                    },
                )
            )
        elif verdict_class == AdmissionVerdict.INSUFFICIENT_STRUCTURAL.value:
            failures.append(
                ConstraintFailure(
                    constraint="can_fit_with_eviction",
                    reason=(
                        "Insufficient reclaimable resources even after evicting all "
                        "currently loaded models"
                    ),
                    details={
                        "vram_free": gateway.vram_free_mb,
                        "vram_total": gateway.vram_total_mb,
                        "ram_free": gateway.ram_free_mb,
                        "ram_total": gateway.ram_total_mb,
                        "loaded_count": len(gateway.loaded_models),
                        "busy_count": len(gateway.busy_models),
                        "retryable": False,
                        "classification_basis": "reclaimable_resources",
                        **reclaimable,
                    },
                )
            )
        else:
            failures.append(
                ConstraintFailure(
                    constraint="eviction_blocked_by_busy_models",
                    reason=(
                        "Capacity shortfall is retryable (transient reservation or "
                        "margin); not structurally impossible on this hardware."
                    ),
                    details={
                        "vram_free": gateway.vram_free_mb,
                        "vram_total": gateway.vram_total_mb,
                        "ram_free": gateway.ram_free_mb,
                        "ram_total": gateway.ram_total_mb,
                        "loaded_count": len(gateway.loaded_models),
                        "busy_count": len(gateway.busy_models),
                        "retryable": True,
                        "classification_basis": "verdict_class",
                        **reclaimable,
                    },
                )
            )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    # T2: Feasible with eviction
    return FeasibilityTier.T2_FEASIBLE_EVICT, (), eviction_plan
