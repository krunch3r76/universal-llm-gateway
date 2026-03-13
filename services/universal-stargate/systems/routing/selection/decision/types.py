"""
Decision engine types: candidates, traces, results.

Immutable types for routing decisions with full observability.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from model_id import ModelId

    from ..types import Gateway
    from .config import AffinityRule


class FeasibilityTier(IntEnum):
    """
    Gateway feasibility classification.

    T0 < T1 < T2 in preference order (lower is worse).
    """

    T0_INFEASIBLE = 0  # Cannot serve model (unhealthy, incompatible, etc.)
    T1_FEASIBLE_NOW = 1  # Can serve model with current resources
    T2_FEASIBLE_EVICT = 2  # Can serve model after eviction


@dataclass(frozen=True, kw_only=True)
class ConstraintFailure:
    """Single constraint that failed for a gateway."""

    constraint: str  # Constraint name (e.g., "has_enough_vram")
    reason: str  # Human-readable reason
    details: dict = field(default_factory=dict)  # Additional context


@dataclass(frozen=True, kw_only=True)
class ScoreComponents:
    """
    Breakdown of utility score components.

    All components before weighting. Final score = Σ(weight_i × component_i).
    """

    affinity: float = 0.0  # Affinity rule match (0 or bonus)
    warm: float = 0.0  # Model loaded (0 or 100)
    slack: float = 0.0  # VRAM slack score (0-10 range)
    contention: float = 0.0  # Active requests (0-10 range, negative after weight)
    staleness: float = 0.0  # Telemetry age penalty (0-10 range, negative after weight)
    stability: float = 0.0  # Hysteresis bonus (0 or small bonus)
    eviction: float = 0.0  # Eviction penalty (negative if eviction needed)
    # Cold-load spreading signals (candidate-load-only)
    empty_gateway: float = 0.0  # 1.0 if no models loaded AND model not warm
    busy_models: float = 0.0  # 0-10 range, only for cold loads (warm = 0.0)

    def total(self, weights) -> float:
        """Calculate weighted total score."""
        return (
            weights.affinity * self.affinity
            + weights.warm * self.warm
            + weights.slack * self.slack
            + weights.contention * self.contention
            + weights.staleness * self.staleness
            + weights.stability * self.stability
            + self.eviction  # Eviction already includes weight
            + weights.empty_gateway * self.empty_gateway
            + weights.busy_models * self.busy_models
        )


@dataclass(frozen=True, kw_only=True)
class EvictionPlanSummary:
    """Summary of eviction plan for a candidate."""

    models_to_evict: frozenset[ModelId]
    freed_vram_mb: int
    freed_ram_mb: int
    estimated_cost: float  # Penalty score for this eviction
    # Observability for hardware-based correction (when applied)
    catalog_freed_vram_mb: int = 0
    hardware_used_vram_mb: int | None = None
    non_evictable_vram_reserve_mb: int = 0
    hardware_correction_applied: bool = False
    # Hysteresis metadata (for event emission by async caller)
    cooldown_protected_count: int = 0
    demand_protected_count: int = 0
    escape_hatch_used: bool = False
    escape_reason: str | None = None
    escape_cooldown_remaining_s: float | None = None
    escape_model_id: str | None = None


@dataclass(frozen=True, kw_only=True)
class GatewayCandidate:
    """
    Gateway with feasibility evaluation and utility score.

    Produced by decision engine for each gateway considered.
    """

    gateway: Gateway
    tier: FeasibilityTier
    constraints_failed: tuple[ConstraintFailure, ...] = ()
    score_components: ScoreComponents | None = None
    eviction_plan: EvictionPlanSummary | None = None

    # Matched affinity rule (if any)
    affinity_rule: AffinityRule | None = None
    _cached_score: float = 0.0

    @property
    def utility_score(self) -> float:
        """Final utility score (0 if infeasible)."""
        if self.score_components is None:
            return 0.0
        # Score is pre-computed with weights
        return self._cached_score

    @property
    def is_feasible(self) -> bool:
        """True if gateway can serve the model (with or without eviction)."""
        return self.tier != FeasibilityTier.T0_INFEASIBLE


@dataclass(frozen=True, kw_only=True)
class DecisionTrace:
    """
    Complete trace of a routing decision.

    Provides full observability for debugging "why did X route to Y?".
    """

    # Request context
    model_id: str
    original_model_id: str | None
    request_id: str | None
    timestamp: float = field(default_factory=time.time)

    # Telemetry snapshot metadata
    snapshot_age_ms: int = 0

    # All candidates evaluated
    candidates: tuple[GatewayCandidate, ...] = ()

    # Selection result
    selected_gateway: str | None = None
    selection_reason: str = ""
    selection_tier: FeasibilityTier | None = None

    # Timing
    evaluation_time_ms: float = 0.0

    # Sticky routing flag
    is_sticky: bool = True

    def to_log_dict(self) -> dict[str, Any]:
        """Convert to dict for structured logging."""
        return {
            "model_id": self.model_id,
            "original_model_id": self.original_model_id,
            "request_id": self.request_id,
            "snapshot_age_ms": self.snapshot_age_ms,
            "candidate_count": len(self.candidates),
            "feasible_count": sum(1 for c in self.candidates if c.is_feasible),
            "selected_gateway": self.selected_gateway,
            "selection_reason": self.selection_reason,
            "selection_tier": self.selection_tier.name if self.selection_tier else None,
            "evaluation_time_ms": self.evaluation_time_ms,
            "is_sticky": self.is_sticky,
        }

    def to_detailed_dict(self) -> dict[str, Any]:
        """Convert to detailed dict including all candidates."""
        result = self.to_log_dict()
        result["candidates"] = [
            {
                "gateway": c.gateway.name,
                "tier": c.tier.name,
                "score": c.utility_score,
                "constraints_failed": [
                    {"constraint": f.constraint, "reason": f.reason}
                    for f in c.constraints_failed
                ],
                "affinity_rule": c.affinity_rule.match if c.affinity_rule else None,
                "eviction_count": (
                    len(c.eviction_plan.models_to_evict) if c.eviction_plan else 0
                ),
            }
            for c in self.candidates
        ]
        return result

    def to_event_payload(self, include_candidates: bool = False) -> dict[str, object]:
        """
        Convert to event payload for emission.

        Args:
            include_candidates: Include full candidate details (expensive)

        Returns:
            Event payload dict for ROUTING_DECISION event
        """
        correction_gateways = [
            c.gateway.name
            for c in self.candidates
            if c.eviction_plan and c.eviction_plan.hardware_correction_applied
        ]

        payload = {
            "model_id": self.model_id,
            "original_model_id": self.original_model_id,
            "selected_gateway": self.selected_gateway,
            "selection_reason": self.selection_reason,
            "selection_tier": self.selection_tier.name if self.selection_tier else None,
            "candidate_count": len(self.candidates),
            "feasible_count": sum(1 for c in self.candidates if c.is_feasible),
            "evaluation_time_ms": self.evaluation_time_ms,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "is_sticky": self.is_sticky,
            "hardware_correction_applied_count": len(correction_gateways),
            "hardware_correction_gateways": correction_gateways,
        }

        if include_candidates:
            payload["candidates"] = [
                {
                    "gateway": c.gateway.name,
                    "tier": c.tier.name,
                    "is_feasible": c.is_feasible,
                    "utility_score": c.utility_score,
                    "loaded_count": len(c.gateway.loaded_models),
                    "loading_count": len(c.gateway.loading_models),
                    "busy_count": len(c.gateway.busy_models),
                    "is_warm": (
                        c.score_components.warm > 0 if c.score_components else False
                    ),
                    "constraints_failed": [
                        {"constraint": f.constraint, "reason": f.reason}
                        for f in (c.constraints_failed or [])
                    ],
                    "score_components": (
                        {
                            "affinity": c.score_components.affinity,
                            "warm": c.score_components.warm,
                            "slack": c.score_components.slack,
                            "contention": c.score_components.contention,
                            "staleness": c.score_components.staleness,
                            "stability": c.score_components.stability,
                            "eviction": c.score_components.eviction,
                            "empty_gateway": c.score_components.empty_gateway,
                            "busy_models": c.score_components.busy_models,
                        }
                        if c.score_components
                        else None
                    ),
                    "eviction_plan": (
                        {
                            "models_to_evict": list(c.eviction_plan.models_to_evict),
                            "freed_vram_mb": c.eviction_plan.freed_vram_mb,
                            "freed_ram_mb": c.eviction_plan.freed_ram_mb,
                            "catalog_freed_vram_mb": (
                                c.eviction_plan.catalog_freed_vram_mb
                            ),
                            "hardware_used_vram_mb": (
                                c.eviction_plan.hardware_used_vram_mb
                            ),
                            "non_evictable_vram_reserve_mb": (
                                c.eviction_plan.non_evictable_vram_reserve_mb
                            ),
                            "hardware_correction_applied": (
                                c.eviction_plan.hardware_correction_applied
                            ),
                        }
                        if c.eviction_plan
                        else None
                    ),
                }
                for c in self.candidates
            ]

        return payload
