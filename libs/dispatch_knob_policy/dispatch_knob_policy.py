"""Shared semantic executor-knob policy (no side effects, no triggers).

Single source of truth mapping a dispatch contract / task-nature to a
recommended executor configuration (model x thinking x effort) as INDEPENDENT
advisory axes (never collapse effort into thinking -- Fork-E, assertion 20513).
Consumed by the Stargate cost-risk alignment path and (later) the handoff +
API-role recommendation adapters. Trigger logic (WHEN to surface a
recommendation) stays in each adapter; this module supplies only the facts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal

from cursor_capabilities import supported_knobs

__all__ = [
    "KnobRecommendation",
    "MECHANICAL_CONTRACTS",
    "recommend_knobs",
    "validate_knobs",
]

MECHANICAL_CONTRACTS: Final[frozenset[str]] = frozenset(
    {"pure-mechanical", "light-bounded"}
)

# Low-cost ALTERNATIVE executor recommended for mechanical/determinate work.
_MECHANICAL_MODEL: Final[str] = "composer-2.5"

RecommendationStatus = Literal["recommended", "none"]


@dataclass(frozen=True, slots=True)
class KnobRecommendation:
    """A semantic executor-knob recommendation.

    ``model`` is the recommended ALTERNATIVE executor model; ``thinking`` and
    ``effort`` are the descriptor-native knob values to apply to the dispatch
    (independent axes). All knob fields are ``None`` when ``status == "none"``.
    """

    status: RecommendationStatus
    model: str | None
    thinking: str | None
    effort: str | None
    rationale_code: str
    rationale: str

    def knob_dict(self) -> dict[str, str]:
        """The {effort, thinking} knob pair; empty when no recommendation."""
        if self.effort is None or self.thinking is None:
            return {}
        return {"effort": self.effort, "thinking": self.thinking}


def recommend_knobs(
    *, contract: str, task_nature: str | None = None
) -> KnobRecommendation:
    """Return the semantic knob recommendation for a dispatch contract.

    Mechanical/determinate contracts (pure-mechanical, light-bounded) recommend
    the low-cost executor with thinking disabled and effort low. Other contracts
    return ``status="none"`` with null knobs (no opinion at this layer).
    ``task_nature`` is accepted for forward compatibility and currently unused.
    """
    if contract in MECHANICAL_CONTRACTS:
        return KnobRecommendation(
            status="recommended",
            model=_MECHANICAL_MODEL,
            thinking="false",
            effort="low",
            rationale_code="mechanical_cost_control",
            rationale=(
                "Mechanical/determinate work should prefer a low-cost executor "
                "with thinking off and effort low unless explicitly escalated."
            ),
        )
    return KnobRecommendation(
        status="none",
        model=None,
        thinking=None,
        effort=None,
        rationale_code="no_policy_opinion",
        rationale="No executor-knob policy opinion for this contract.",
    )


def validate_knobs(*, model_id: str, knobs: Mapping[str, str]) -> dict[str, str]:
    """Validate knob name/value pairs against a model capability descriptor.

    Returns a mapping knob-name -> one of {"valid", "unsupported_knob",
    "invalid_value"}. Advisory only; never raises. ``model_id`` must be the
    canonical Cursor wire id (descriptor key in ``cursor_capabilities``).
    """
    specs = supported_knobs(model_id)
    result: dict[str, str] = {}
    for name, value in knobs.items():
        spec = specs.get(name)
        if spec is None:
            result[name] = "unsupported_knob"
        elif value not in spec.accepted:
            result[name] = "invalid_value"
        else:
            result[name] = "valid"
    return result
