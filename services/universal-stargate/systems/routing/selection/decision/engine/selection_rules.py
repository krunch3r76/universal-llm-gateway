"""Selection-rule helpers for choosing the best feasible gateway candidate.

This module keeps deterministic tier and affinity arbitration logic isolated
from orchestration code so the engine can evaluate candidates separately from
final selection policy application.
"""

from __future__ import annotations

from universal_logging import get_logger

from ..types import FeasibilityTier, GatewayCandidate

logger = get_logger(__name__)


def apply_selection_rule(
    *,
    candidates: list[GatewayCandidate],
    hard_affinity,
    eviction_margin: float,
) -> tuple[GatewayCandidate | None, str]:
    """Select a winning gateway candidate from evaluated feasibility tiers.

    Rules are ordered by strictness: hard affinity filtering, then tier
    preference, then score and deterministic name tie-breakers. The function
    returns both the selected candidate and a reason string for tracing.
    """
    t1_candidates = [c for c in candidates if c.tier == FeasibilityTier.T1_FEASIBLE_NOW]
    t2_candidates = [
        c for c in candidates if c.tier == FeasibilityTier.T2_FEASIBLE_EVICT
    ]

    def sort_key(candidate: GatewayCandidate) -> tuple[float, str]:
        return (-candidate.utility_score, candidate.gateway.name)

    if hard_affinity:
        t1_affinity = [
            c for c in t1_candidates if c.gateway.node_id == hard_affinity.node
        ]
        t2_affinity = [
            c for c in t2_candidates if c.gateway.node_id == hard_affinity.node
        ]
        t1_affinity_sorted = sorted(t1_affinity, key=sort_key)
        t2_affinity_sorted = sorted(t2_affinity, key=sort_key)

        if t1_affinity_sorted:
            return t1_affinity_sorted[0], f"hard_affinity={hard_affinity.node}, tier=T1"

        if t2_affinity_sorted and hard_affinity.evict_if_needed:
            return (
                t2_affinity_sorted[0],
                f"hard_affinity={hard_affinity.node}, tier=T2_eviction",
            )

        logger.warning(
            "Hard affinity node %s infeasible (no T1/T2 candidates). "
            "Returning None to trigger wait/error logic.",
            hard_affinity.node,
        )
        return None, f"hard_affinity={hard_affinity.node}_infeasible"

    t1_sorted = sorted(t1_candidates, key=sort_key)
    t2_sorted = sorted(t2_candidates, key=sort_key)
    best_t1 = t1_sorted[0] if t1_sorted else None
    best_t2 = t2_sorted[0] if t2_sorted else None

    if best_t1 and best_t2:
        if best_t2.utility_score >= best_t1.utility_score + eviction_margin:
            return (
                best_t2,
                f"T2 preferred (score={best_t2.utility_score:.1f} >= "
                f"T1 {best_t1.utility_score:.1f} + margin {eviction_margin})",
            )
        return (
            best_t1,
            f"T1 preferred (score={best_t1.utility_score:.1f}, "
            f"T2 would need {best_t1.utility_score + eviction_margin:.1f})",
        )

    if best_t1:
        return best_t1, f"T1 selected (score={best_t1.utility_score:.1f})"
    if best_t2:
        return (
            best_t2,
            f"T2 selected (score={best_t2.utility_score:.1f}, no T1 available)",
        )
    return None, "no_feasible_gateways"
