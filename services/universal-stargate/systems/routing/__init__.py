"""
Routing System - Gateway selection and model routing.

This system handles:
- Feasibility evaluation (T0/T1/T2 tiers)
- Gateway scoring and selection
- Eviction planning
- Request queue management

Key Invariants:
    ∀ gateway: (model_loaded ∧ has_capacity) ⟹ tier = T1
    ∀ gateway: (model_loaded ∧ ¬has_capacity) ⟹ tier = T0
    ∀ eviction: models_to_evict ⊆ idle_models

Usage:
    from systems.routing import DecisionEngine
    from systems.routing.selection.decision import evaluate_feasibility
"""

from .model_router import ModelRouter
from .selection.decision.engine import DecisionEngine
from .selection.decision.feasibility import evaluate_feasibility
from .selection.decision.scorer import calculate_utility
from .selection.decision.types import FeasibilityTier, ScoreComponents

__all__ = [
    # Core
    "ModelRouter",
    "DecisionEngine",
    # Feasibility
    "FeasibilityTier",
    "evaluate_feasibility",
    # Scoring
    "ScoreComponents",
    "calculate_utility",
]
