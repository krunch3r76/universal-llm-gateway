"""
Feasibility evaluation - classify gateways into tiers.

Evaluates ALL gateways including those needing eviction.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

from .eviction_planning import _compute_eviction_plan
from .model_checks import _is_model_available, _is_model_loaded
from .resource_checks import _check_resources
from .types import ConstraintFailure, EvictionPlanSummary, FeasibilityTier

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .config import RoutingPolicy
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)


def evaluate_feasibility(
    gateway: Gateway,
    placement: Placement,
    policy: RoutingPolicy,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    sticky: bool = True,
    routing_key_tracker: RoutingKeyTracker | None = None,
    is_gateway_available_fn: Callable[[str], bool] | None = None,
) -> tuple[
    FeasibilityTier,
    tuple[ConstraintFailure, ...],
    EvictionPlanSummary | None,
]:
    """
    Evaluate gateway feasibility for model placement.

    Returns (tier, constraint_failures, eviction_plan).

    Admission control: CapacityLedger in systems/routing/capacity/

    Invariant: tier == T0 ⟹ len(constraint_failures) > 0
    Invariant: tier == T2 ⟹ eviction_plan is not None
    Invariant (non-sticky): ¬sticky ⟹
               T2_FEASIBLE_EVICT valid even when model loaded elsewhere

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
        routing_key_tracker: For eviction protection of in-flight models
    """
    logger.info(
        f"🔍 Evaluating feasibility: {placement.model_id} on {gateway.name} "
        f"(VRAM: {gateway.vram_free_mb}/{gateway.vram_total_mb}MB, "
        f"active_requests: {gateway.active_requests}, sticky={sticky})"
    )
    failures: list[ConstraintFailure] = []

    # Check 0: Circuit Breaker (Prioritize before health check)
    if is_gateway_available_fn:
        if not is_gateway_available_fn(gateway.name):
            logger.warning(f"❌ Circuit OPEN for {gateway.name} - marking INFEASIBLE")
            failures.append(
                ConstraintFailure(
                    constraint="circuit_breaker",
                    reason=f"Circuit OPEN for {gateway.name}",
                    details={"circuit_open": True},
                )
            )
            return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    # Check 1: Gateway health
    if gateway.health_score < 0.5:
        failures.append(
            ConstraintFailure(
                constraint="is_healthy",
                reason=f"Health score {gateway.health_score:.2f} < 0.5",
                details={"health_score": gateway.health_score},
            )
        )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    # Check 2: Model in catalog
    model_available = _is_model_available(gateway, placement)
    if not model_available:
        logger.info(
            f"❌ Model {placement.model_id} NOT in {gateway.name} catalog. "
            f"Catalog sample: {list(gateway.available_models)[:10]}"
        )
        failures.append(
            ConstraintFailure(
                constraint="has_model_available",
                reason=f"Model {placement.model_id} not in gateway catalog",
                details={"available_models": list(gateway.available_models)[:5]},
            )
        )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None
    else:
        logger.info(f"✅ Model {placement.model_id} found in {gateway.name} catalog")

    # Check 3: Model already loaded (fast path)
    # ∀ loaded model: busy ⟹ T0 (capacity) — token counting is the first step of
    # inference and returns 503 while the model is executing another request.
    if _is_model_loaded(gateway, placement):
        if placement.model_id in gateway.busy_models:
            logger.info(
                f"⏳ Model {placement.model_id} loaded but busy on {gateway.name} "
                f"(active_requests={gateway.active_requests}) — T0 (capacity)"
            )
            failures.append(
                ConstraintFailure(
                    constraint="has_gateway_capacity",
                    reason=f"Model {placement.model_id} is busy on {gateway.name}",
                    details={
                        "busy": True,
                        "active_requests": gateway.active_requests,
                    },
                )
            )
            return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None
        logger.info(
            f"✅ Model {placement.model_id} loaded and idle on {gateway.name} "
            f"(active_requests={gateway.active_requests})"
        )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    # Check 4: Sufficient resources without eviction
    logger.debug(
        f"🔍 FEASIBILITY Check 4 (resources): Checking if {placement.model_id} "
        f"fits on {gateway.name} without eviction"
    )

    resource_margins_config = {"resource_margins": policy.resource_margins}
    has_resources, resource_failure = _check_resources(
        gateway,
        placement,
        requirements_lookup,
        config=resource_margins_config,
    )

    if has_resources:
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
        config=resource_margins_config,
        routing_key_tracker=routing_key_tracker,
    )

    if eviction_plan is None:
        # Cannot fit even with full eviction
        if resource_failure is not None:
            failures.append(resource_failure)
        failures.append(
            ConstraintFailure(
                constraint="can_fit_with_eviction",
                reason="Cannot fit even after evicting all idle models",
                details={
                    "vram_free": gateway.vram_free_mb,
                    "ram_free": gateway.ram_free_mb,
                },
            )
        )
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    # T2: Feasible with eviction
    return FeasibilityTier.T2_FEASIBLE_EVICT, (), eviction_plan
