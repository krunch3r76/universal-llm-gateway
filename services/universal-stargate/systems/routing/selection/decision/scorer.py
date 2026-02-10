"""
Utility scoring - calculate preference scores for feasible gateways.

All score components tracked separately for observability.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .types import EvictionPlanSummary, FeasibilityTier, ScoreComponents

if TYPE_CHECKING:
    from ..types import Gateway, Placement
    from .config import AffinityRule, RoutingPolicy

logger = get_logger(__name__)


def calculate_utility(
    gateway: Gateway,
    placement: Placement,
    policy: RoutingPolicy,
    tier: FeasibilityTier,
    eviction_plan: EvictionPlanSummary | None,
    affinity_rule: AffinityRule | None,
    current_best: str | None = None,
    sticky: bool = True,
) -> tuple[ScoreComponents, float]:
    """
    Calculate utility score for a feasible gateway.

    Returns (components, weighted_total).

    Args:
        gateway: Gateway to score
        placement: Model placement requirements
        policy: Routing policy with weights
        tier: Gateway's feasibility tier
        eviction_plan: Eviction plan if T2
        affinity_rule: Matched affinity rule if any
        current_best: Current best gateway for stability bonus
        sticky: Whether this model uses sticky routing (affects load spreading)
    """
    weights = policy.weights

    # Component: Affinity
    affinity_score = 0.0
    if affinity_rule and affinity_rule.stargate == gateway.name:
        affinity_score = affinity_rule.bonus

    # Component: Warm (model already loaded)
    #
    # Sticky coalescing:
    # For sticky models, treat "loading" as effectively warm so concurrent
    # requests coalesce to the same gateway and avoid dual-load races.
    is_loaded = _is_model_loaded(gateway, placement)
    is_loading = _is_model_loading(gateway, placement)
    is_effectively_warm = is_loaded or (sticky and is_loading)

    warm_score = 100.0 if is_effectively_warm else 0.0  # Avoid load latency/races

    # Component: Slack (VRAM headroom)
    slack_score = _calculate_slack_score(gateway, placement, eviction_plan)

    # Component: Contention (active requests)
    contention_score = _calculate_contention_score(gateway)

    # Component: Staleness (telemetry age)
    staleness_score = _calculate_staleness_score(gateway, policy)

    # Component: Stability (hysteresis)
    # INVARIANT: sticky ⟹ stability bonus; ¬sticky ⟹ no stability bonus
    # Nonsticky models should spread across gateways, not stick to one
    stability_score = 0.0
    if sticky and current_best and gateway.name == current_best:
        stability_score = 1.0  # Small bonus for current best (sticky only)

    # Component: Eviction penalty
    eviction_score = 0.0
    if tier == FeasibilityTier.T2_FEASIBLE_EVICT and eviction_plan:
        eviction_count = len(eviction_plan.models_to_evict)
        eviction_score = weights.eviction_base + (
            weights.eviction_per_model * eviction_count
        )

    # Component: Cold-load spreading
    # Only apply for cold loads (model not already loaded)
    is_warm = is_loaded

    # empty_gateway: 1.0 if gateway has no models, 0.0 otherwise
    # Only matters for cold loads - if warm, model is already there
    # CRITICAL: Consider both loaded AND loading models to prevent routing
    # simultaneous requests to the same gateway before telemetry arrives
    empty_gateway_score = 0.0
    if (
        not is_effectively_warm
        and len(gateway.loaded_models) + len(gateway.loading_models) == 0
    ):
        empty_gateway_score = 1.0

    # busy_models: Candidate-load penalty (fewer busy = higher score)
    # Only applies when model must be loaded — warm gateways get no penalty
    # This prevents busy-count fluctuations from overriding warm preference
    # CRITICAL: Include loading_models to prevent routing simultaneous cold loads
    # to the same gateway
    busy_models_score = 0.0
    if not is_effectively_warm:
        busy_count = len(gateway.busy_models) + len(gateway.loading_models)
        busy_models_score = max(0.0, 10.0 - min(busy_count, 10))

    # Component: Nonsticky load spreading penalty
    # For nonsticky models, strongly penalize gateways that are already loading
    # THIS specific model. This encourages distributing nonsticky model loads
    # across multiple gateways instead of coalescing all requests to one gateway.
    # INVARIANT: ¬sticky ∧ model ∈ gateway.loading_models ⟹ heavy penalty
    nonsticky_loading_penalty = 0.0
    if not sticky and not is_loaded:
        # Check if THIS model is already loading on this gateway
        if is_loading:
            # Apply heavy penalty to discourage routing here
            # This will cause subsequent requests to prefer other gateways
            nonsticky_loading_penalty = -1000.0
            logger.info(
                f"⚠️ NONSTICKY LOAD SPREADING: {placement.model_id} already "
                f"loading on {gateway.name}, applying penalty to spread load"
            )

    # Component: Nonsticky warm jitter
    # For nonsticky models already loaded on multiple gateways, add small random
    # jitter to break ties and spread requests across gateways.
    # Without this, deterministic tie-breaking (gateway name) causes all requests
    # to route to the same gateway until telemetry updates with new contention.
    # INVARIANT: ¬sticky ∧ is_warm ⟹ random jitter applied
    nonsticky_warm_jitter = 0.0
    if not sticky and is_warm:
        # Small jitter (0-0.5) to break ties without overriding real score diffs
        # Range chosen to be smaller than stability bonus (1.0) but large enough
        # to break ties between gateways with identical scores
        nonsticky_warm_jitter = random.uniform(0.0, 0.5)
        logger.debug(
            f"🎲 NONSTICKY WARM JITTER: {placement.model_id} on {gateway.name} "
            f"jitter={nonsticky_warm_jitter:.3f}"
        )

    components = ScoreComponents(
        affinity=affinity_score,
        warm=warm_score,
        slack=slack_score,
        contention=contention_score,
        staleness=staleness_score,
        stability=stability_score,
        eviction=eviction_score,
        empty_gateway=empty_gateway_score,
        busy_models=busy_models_score,
    )

    # Calculate weighted total
    weighted_total = (
        weights.affinity * affinity_score
        + weights.warm * warm_score
        + weights.slack * slack_score
        + weights.contention * contention_score
        + weights.staleness * staleness_score
        + weights.stability * stability_score
        + eviction_score  # Already includes weight
        + weights.empty_gateway * empty_gateway_score
        + weights.busy_models * busy_models_score
        + nonsticky_loading_penalty  # No weight - direct penalty
        + nonsticky_warm_jitter  # No weight - direct jitter for load spreading
    )

    # Diagnostic logging for score breakdown
    logger.info(
        f"SCORE BREAKDOWN for {placement.model_id} on {gateway.name}:\n"
        f"  affinity:        {affinity_score:7.1f} x {weights.affinity:5.1f} = "
        f"{weights.affinity * affinity_score:8.1f}\n"
        f"  warm:            {warm_score:7.1f} x {weights.warm:5.1f} = "
        f"{weights.warm * warm_score:8.1f}\n"
        f"  slack:           {slack_score:7.1f} x {weights.slack:5.1f} = "
        f"{weights.slack * slack_score:8.1f}\n"
        f"  contention:      {contention_score:7.1f} x {weights.contention:5.1f} = "
        f"{weights.contention * contention_score:8.1f}\n"
        f"  staleness:       {staleness_score:7.1f} x {weights.staleness:5.1f} = "
        f"{weights.staleness * staleness_score:8.1f}\n"
        f"  stability:       {stability_score:7.1f} x {weights.stability:5.1f} = "
        f"{weights.stability * stability_score:8.1f}\n"
        f"  eviction:        {eviction_score:7.1f} (includes weight)\n"
        f"  empty_gateway:   {empty_gateway_score:7.1f} x "
        f"{weights.empty_gateway:5.1f} = "
        f"{weights.empty_gateway * empty_gateway_score:8.1f}\n"
        f"  busy_models:     {busy_models_score:7.1f} x "
        f"{weights.busy_models:5.1f} = "
        f"{weights.busy_models * busy_models_score:8.1f}\n"
        f"  nonsticky_penalty: {nonsticky_loading_penalty:7.1f} (direct)\n"
        f"  nonsticky_jitter:  {nonsticky_warm_jitter:7.3f} (direct)\n"
        f"  ─────────────────────────────────────────\n"
        f"  TOTAL:           {weighted_total:8.1f}"
    )

    return components, weighted_total


def _is_model_loaded(gateway: Gateway, placement: Placement) -> bool:
    """Check if model is already loaded."""
    if placement.original_model_id:
        from model_id import ModelId

        original_parsed = ModelId.parse(placement.original_model_id)
        if original_parsed in gateway.loaded_models:  # ModelId in frozenset[ModelId]
            return True
    return placement.model_id in gateway.loaded_models  # ModelId in frozenset[ModelId]


def _is_model_loading(gateway: Gateway, placement: Placement) -> bool:
    """Check if model is currently loading on gateway."""
    if placement.original_model_id:
        from model_id import ModelId

        original_parsed = ModelId.parse(placement.original_model_id)
        if original_parsed in gateway.loading_models:  # ModelId in frozenset[ModelId]
            return True
    return placement.model_id in gateway.loading_models  # ModelId in frozenset[ModelId]


def _calculate_slack_score(
    gateway: Gateway,
    placement: Placement,
    eviction_plan: EvictionPlanSummary | None,
) -> float:
    """
    Calculate slack score (headroom after placement).

    Higher slack = better (more room for future models).
    Normalized to 0-10 range.
    """
    if placement.is_gpu:
        free = gateway.vram_free_mb
        if eviction_plan:
            free += eviction_plan.freed_vram_mb
        slack = free - placement.vram_mb
    else:
        free = gateway.ram_free_mb
        if eviction_plan:
            free += eviction_plan.freed_ram_mb
        slack = free - int(placement.ram_mb * 1.10)

    # Normalize: 0 slack = 0, 10GB slack = 10
    return max(0.0, min(10.0, slack / 1000.0))


def _calculate_contention_score(gateway: Gateway) -> float:
    """
    Calculate contention score (fewer requests = better).

    Normalized to 0-10 range.
    """
    # Fewer requests = higher score
    # Assume max 100 concurrent requests for normalization
    return max(0.0, 10.0 - gateway.active_requests / 10.0)


def _calculate_staleness_score(
    gateway: Gateway,
    policy: RoutingPolicy,
) -> float:
    """
    Calculate staleness penalty.

    Returns 0-10 range (0 = fresh, 10 = stale).
    """
    if gateway.telemetry_timestamp == 0.0:
        return 0.0  # Unknown, no penalty

    age_ms = gateway.telemetry_age_ms
    max_age = policy.telemetry_max_age_ms

    if age_ms <= max_age:
        return 0.0  # Fresh enough

    # Linear penalty up to 2x max age
    excess = age_ms - max_age
    penalty = min(10.0, excess / max_age * 10.0)

    return penalty
