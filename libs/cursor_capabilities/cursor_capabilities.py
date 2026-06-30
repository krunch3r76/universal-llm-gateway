"""Shared plain-data Cursor model capability descriptor (no cursor_sdk dependency)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

__all__ = [
    "CURSOR_MODEL_CAPABILITIES",
    "DESCRIPTOR_VERSION",
    "KnobSpec",
    "ModelCapability",
    "default_variant",
    "supported_knobs",
]

DESCRIPTOR_VERSION: Final[str] = "2026-06-30"


@dataclass(frozen=True, slots=True)
class KnobSpec:
    accepted: tuple[str, ...]
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ModelCapability:
    knobs: Mapping[str, KnobSpec]
    default_variant: Mapping[str, str]


CURSOR_MODEL_CAPABILITIES: Final[dict[str, ModelCapability]] = {
    "composer-2.5": ModelCapability(
        knobs={
            "fast": KnobSpec(accepted=("false", "true"), default="true"),
        },
        default_variant={"fast": "true"},
    ),
    "claude-opus-4-8": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("300k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "xhigh", "max")),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        default_variant={
            "cyber": "false",
            "thinking": "true",
            "context": "1m",
            "effort": "high",
            "fast": "false",
        },
    ),
    "claude-sonnet-4-6": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("200k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "max")),
        },
        default_variant={
            "thinking": "true",
            "context": "1m",
            "effort": "medium",
        },
    ),
    # Routed-but-untrusted consult models (team_dispatch reviewer/skeptic/cheap-recon
    # targets) promoted into the trusted dispatch allowlist. Allowlist = trust/route
    # boundary, not a catalog mirror — narrow set wins on reversibility + trust hygiene
    # (thread 3765 Task B; decision:cursor-sdk-018-feature-uptake).
    "gpt-5.5": ModelCapability(
        knobs={
            "context": KnobSpec(accepted=("272k", "1m")),
            "reasoning": KnobSpec(
                accepted=("none", "low", "medium", "high", "extra-high")
            ),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        default_variant={"context": "1m", "reasoning": "medium", "fast": "false"},
    ),
    "grok-4.3": ModelCapability(
        knobs={
            "context": KnobSpec(accepted=("200k", "1m")),
        },
        default_variant={"context": "1m"},
    ),
    "gemini-3.5-flash": ModelCapability(
        knobs={},
        default_variant={},
    ),
}


def supported_knobs(model_id: str) -> Mapping[str, KnobSpec]:
    """Return knob specs for a canonical Cursor wire model id."""
    cap = CURSOR_MODEL_CAPABILITIES.get(model_id)
    if cap is None:
        return {}
    return cap.knobs


def default_variant(model_id: str) -> Mapping[str, str]:
    """Return the catalog default variant for a canonical Cursor wire model id."""
    cap = CURSOR_MODEL_CAPABILITIES.get(model_id)
    if cap is None:
        return {}
    return cap.default_variant
