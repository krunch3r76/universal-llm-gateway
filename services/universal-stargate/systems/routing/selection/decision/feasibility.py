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
from .resource_checks import (
    _calculate_required_resources,
    _check_resources,
    _compute_loading_reservation,
    resolve_gateway_requirements,
)
from .types import ConstraintFailure, EvictionPlanSummary, FeasibilityTier

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .config import RoutingPolicy
    from .protocols import RoutingKeyTracker

logger = get_logger(__name__)

_COLD_LOAD_MIN_SLACK_MB = 1024


def _can_fit_after_eviction_including_busy(
    gateway: Gateway,
    placement: Placement,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    resource_margins: dict[str, float] | None = None,
) -> tuple[bool, dict[str, int]]:
    """Return True iff reclaimable resources can fit target after eviction.

    Distinguishes transient eviction failure (capacity is reclaimable once loaded
    models can be evicted) from permanent (insufficient reclaimable capacity).

    Returns:
        tuple[bool, dict[str, int]]:
            - can_fit: True if reclaimable VRAM/RAM can satisfy requirement
            - diagnostics: reclaimable, required, and deficit values
    """
    resolved = resolve_gateway_requirements(gateway, placement)
    if isinstance(resolved, ConstraintFailure):
        return False, {}
    gw_vram_mb, gw_ram_mb = resolved

    margins = resource_margins or {}
    ram_margin_pct = int(margins.get("ram_margin_pct", 3))
    vram_margin_pct = int(margins.get("vram_margin_pct", 5))
    vram_headroom_mb = int(margins.get("vram_headroom_mb", 2048))
    ram_needed = int(gw_ram_mb * (1.0 + ram_margin_pct / 100))
    vram_pct = int(gw_vram_mb * (1.0 + vram_margin_pct / 100))
    vram_needed = (vram_pct + vram_headroom_mb) if gw_vram_mb > 0 else 0

    # Mirror _check_resources / _compute_eviction_plan: loading models consume
    # VRAM/RAM that is NOT reclaimable by eviction. Not subtracting here caused
    # the classifier to flip from transient (eviction_blocked_by_busy_models) to
    # permanent (can_fit_with_eviction) on the first wait-loop retry whenever a
    # parallel load started between rejection and retry — producing silent
    # waited_ms=0 bails for knife-edge models (e.g. 14b/26b variants).
    vram_reserved = 0
    ram_reserved = 0
    if gateway.loading_models:
        try:
            vram_reserved, ram_reserved, _ = _compute_loading_reservation(
                gateway, placement.model_id, requirements_lookup
            )
        except ValueError:
            # Missing requirements for a loading model: fail-conservative —
            # downgrade to can_fit_with_eviction (permanent). Same rationale
            # as _check_resources' loading_model_requirements_missing path.
            return False, {}

    effective_vram_free = gateway.vram_free_mb - vram_reserved
    effective_ram_free = gateway.ram_free_mb - ram_reserved

    reclaimable_vram = effective_vram_free
    reclaimable_ram = effective_ram_free
    for loaded_model_id in gateway.loaded_models:
        measured_vram = gateway.model_measured_vram.get(loaded_model_id)
        catalog_vram, catalog_ram = gateway.get_model_resource_usage(loaded_model_id)
        # requirements_lookup provides the *catalog* requirements for the model.
        # We use it here to ensure we account for the *minimum* resources a model
        # would consume if it were loaded, even if measured_vram is absent.
        catalog_req_vram_mb, catalog_req_ram_mb = requirements_lookup(loaded_model_id)
        effective_vram = (
            measured_vram
            if measured_vram is not None
            else max(catalog_req_vram_mb, catalog_vram)
        )
        effective_ram = catalog_req_ram_mb if catalog_req_ram_mb > 0 else catalog_ram
        reclaimable_vram += max(effective_vram, 0)
        reclaimable_ram += max(effective_ram, 0)

    vram_ok = vram_needed <= 0 or reclaimable_vram >= vram_needed
    ram_ok = ram_needed <= 0 or reclaimable_ram >= ram_needed

    diagnostics = {
        "max_freeable_vram": reclaimable_vram,
        "required_vram": vram_needed,
        "vram_deficit_mb": max(0, vram_needed - reclaimable_vram),
        "max_freeable_ram": reclaimable_ram,
        "required_ram": ram_needed,
        "ram_deficit_mb": max(0, ram_needed - reclaimable_ram),
        "vram_reserved_loading": vram_reserved,
        "ram_reserved_loading": ram_reserved,
    }
    return vram_ok and ram_ok, diagnostics


def evaluate_feasibility(
    gateway: Gateway,
    placement: Placement,
    policy: RoutingPolicy,
    requirements_lookup: Callable[[ModelId], tuple[int, int]],
    sticky: bool = True,
    routing_key_tracker: RoutingKeyTracker | None = None,
    is_gateway_available_fn: Callable[[str, str], bool] | None = None,
    eviction_cooldown_s: float = 120.0,
    has_demand: Callable[[str], bool] | None = None,
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
    """
    logger.info(
        f"🔍 Evaluating feasibility: {placement.model_id} on {gateway.name} "
        f"(VRAM: {gateway.vram_free_mb}/{gateway.vram_total_mb}MB, "
        f"active_requests: {gateway.active_requests}, sticky={sticky})"
    )
    failures: list[ConstraintFailure] = []

    # Check 0: Circuit Breaker (Prioritize before health check)
    if is_gateway_available_fn:
        if not is_gateway_available_fn(gateway.name, str(placement.model_id)):
            logger.warning(f"❌ Circuit OPEN for {gateway.name} - marking INFEASIBLE")
            failures.append(
                ConstraintFailure(
                    constraint="circuit_breaker",
                    reason=f"Circuit OPEN for {gateway.name}",
                    details={
                        "circuit_open": True,
                        "model_id": str(placement.model_id),
                        "retryable": True,
                    },
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
    # Admission control handled by CapacityPool (master-local, count-based).
    # busy_models is a telemetry hint used for scoring and eviction protection
    # but NOT as a hard gate — admission is handled by CapacityPool.
    # TTL self-heal fires at read time (get_busy_models()); eviction reconciles
    # separately via routing_key_tracker → actually_busy_models.
    if _is_model_loaded(gateway, placement):
        if placement.model_id in gateway.busy_models:
            logger.info(
                f"📊 Model {placement.model_id} loaded (busy per telemetry) on "
                f"{gateway.name} — T1 (CapacityPool handles admission)"
            )
        else:
            logger.info(
                f"✅ Model {placement.model_id} loaded and idle on {gateway.name}"
            )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

    # Check 3.5: Model is actively loading on this gateway (cold-load in progress)
    # ∀ m ∈ loading_models: Stargate initiated the load via mark_loading_optimistic.
    # Treat as T1: CapacityPool exposes only the loading-phase placeholder while
    # the model is still non-runnable; ensure_model_loaded_on_remote waits for
    # real load completion, and restore_model_capacity expands concurrency once
    # the model is confirmed loaded.
    # Bypassing the VRAM check here is correct — VRAM is consumed by the loading
    # model itself, and routing elsewhere would violate the sticky invariant.
    if placement.model_id in gateway.loading_models:
        logger.info(
            f"✅ Model {placement.model_id} loading on {gateway.name} — T1 "
            f"(CapacityPool placeholder guards; "
            f"ensure_model_loaded_on_remote will wait)"
        )
        return FeasibilityTier.T1_FEASIBLE_NOW, (), None

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
        else:
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
        return FeasibilityTier.T0_INFEASIBLE, tuple(failures), None

    # T2: Feasible with eviction
    return FeasibilityTier.T2_FEASIBLE_EVICT, (), eviction_plan
