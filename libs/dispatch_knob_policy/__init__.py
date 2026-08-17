"""Shared semantic executor-knob policy, consumed across dispatch surfaces."""

from .dispatch_knob_policy import (
    MECHANICAL_CONTRACTS,
    KnobRecommendation,
    recommend_knobs,
    validate_knobs,
)
from .executor_recommendation import (
    SCHEMA_VERSION,
    build_executor_recommendation,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('stargate',)

__all__ = [
    "KnobRecommendation",
    "MECHANICAL_CONTRACTS",
    "SCHEMA_VERSION",
    "build_executor_recommendation",
    "recommend_knobs",
    "validate_knobs",
]
