"""
Affinity-aware routing decision engine.

Unified decision engine that evaluates feasibility for all gateways,
scores utility with explicit affinity rules, and produces structured traces.
"""

from __future__ import annotations

from .config import (
    AffinityRule,
    AffinityStrength,
    RoutingPolicy,
    ScoringWeights,
    load_routing_policy,
)
from .engine import DecisionEngine, create_decision_engine
from .feasibility import evaluate_feasibility
from .scorer import calculate_utility
from .stability import StickyPlacementTracker
from .types import (
    ConstraintFailure,
    DecisionTrace,
    EvictionPlanSummary,
    FeasibilityTier,
    GatewayCandidate,
    ScoreComponents,
)

__all__ = [
    "AffinityRule",
    "AffinityStrength",
    "ConstraintFailure",
    "DecisionEngine",
    "DecisionTrace",
    "EvictionPlanSummary",
    "FeasibilityTier",
    "GatewayCandidate",
    "RoutingPolicy",
    "ScoreComponents",
    "ScoringWeights",
    "StickyPlacementTracker",
    "calculate_utility",
    "create_decision_engine",
    "evaluate_feasibility",
    "load_routing_policy",
]
