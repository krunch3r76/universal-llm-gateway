"""Shared plain-data Cursor model capability descriptor (no cursor_sdk dependency)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "CURSOR_DENIED_MODELS",
    "CURSOR_MODEL_CAPABILITIES",
    "DESCRIPTOR_VERSION",
    "KnobSpec",
    "ModelCapability",
    "canonical_cursor_bare_id",
    "catalog_divergences",
    "default_variant",
    "is_cursor_model_denied",
    "supported_knobs",
    "to_model_card_dict",
]

DESCRIPTOR_VERSION: Final[str] = "2026-07-21"

# Emergency denylist for cursor-sdk substrate admission. Entries are bare wire ids
# (no cursor/ prefix); membership is checked after prefix strip + lowercase.
CURSOR_DENIED_MODELS: Final[frozenset[str]] = frozenset()


@dataclass(frozen=True, slots=True)
class KnobSpec:
    accepted: tuple[str, ...]
    default: str | None = None


@dataclass(frozen=True, slots=True)
class ModelCapability:
    knobs: Mapping[str, KnobSpec]
    default_variant: Mapping[str, str]
    fixed_params: Mapping[str, str] = field(default_factory=dict)
    instruction_profile: str = "mechanical"


def _knob_card_entry(spec: KnobSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {"accepted": list(spec.accepted)}
    if spec.default is not None:
        entry["default"] = spec.default
    return entry


def to_model_card_dict(cap: ModelCapability) -> dict[str, Any]:
    """Neutral model-card projection for the cursor-sdk substrate.

    Shared key vocabulary: ``knobs``, ``fixed_params``, and ``api_surface`` only
    on the cloud side. Distinct from provider wire-body projection.
    """
    return {
        "knobs": {name: _knob_card_entry(spec) for name, spec in cap.knobs.items()},
        "fixed_params": dict(cap.fixed_params),
        "instruction_profile": cap.instruction_profile,
    }


def catalog_divergences(
    live_catalog: Mapping[str, Mapping[str, object]],
) -> list[str]:
    """Compare a projected live catalog against ``CURSOR_MODEL_CAPABILITIES``."""
    errors: list[str] = []
    for model_id, capability in CURSOR_MODEL_CAPABILITIES.items():
        live = live_catalog.get(model_id)
        if live is None:
            errors.append(f"missing model {model_id!r} in live catalog")
            continue
        live_knobs = live.get("knobs")
        if not isinstance(live_knobs, Mapping):
            errors.append(f"model {model_id!r}: live knobs not a mapping")
            continue
        for knob_name, spec in capability.knobs.items():
            live_values = live_knobs.get(knob_name)
            if live_values is None:
                errors.append(f"model {model_id!r}: missing knob {knob_name!r}")
                continue
            if tuple(live_values) != spec.accepted:
                errors.append(
                    f"model {model_id!r}: knob {knob_name!r} accepted "
                    f"{tuple(live_values)!r} != descriptor {spec.accepted!r}"
                )
        live_default = live.get("default_variant")
        if not isinstance(live_default, Mapping):
            errors.append(f"model {model_id!r}: live default_variant not a mapping")
            continue
        if dict(live_default) != dict(capability.default_variant):
            errors.append(
                f"model {model_id!r}: default_variant "
                f"{dict(live_default)!r} != descriptor "
                f"{dict(capability.default_variant)!r}"
            )
    return errors


def is_judgment_profile_mismatch(*, role: str, model: str) -> bool:
    """True when a mechanical cursor model is requested on a judgment role."""
    from implement_admission.check_review_substrate import (
        CheckReviewAdmissionReject,
        evaluate_check_review_admission,
    )

    verdict = evaluate_check_review_admission(
        role,
        model,
        api_role_with_cursor_on_api_profile=True,
    )
    return isinstance(verdict, CheckReviewAdmissionReject) and verdict.code == (
        "profile_mismatch"
    )


CURSOR_MODEL_CAPABILITIES: Final[dict[str, ModelCapability]] = {
    "composer-2.5": ModelCapability(
        knobs={
            "fast": KnobSpec(accepted=("false", "true"), default="true"),
        },
        default_variant={"fast": "true"},
        instruction_profile="mechanical",
    ),
    "claude-opus-5": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("300k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "xhigh", "max")),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        fixed_params={"cyber": "false"},
        default_variant={
            "thinking": "true",
            "context": "1m",
            "effort": "high",
            "fast": "false",
        },
        instruction_profile="reasoner",
    ),
    "claude-opus-4-8": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("300k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "xhigh", "max")),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        fixed_params={"cyber": "false"},
        default_variant={
            "thinking": "true",
            "context": "1m",
            "effort": "high",
            "fast": "false",
        },
        instruction_profile="reasoner",
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
        instruction_profile="reasoner",
    ),
    "claude-sonnet-5": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("300k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "xhigh", "max")),
        },
        default_variant={
            "thinking": "true",
            "context": "1m",
            "effort": "high",
        },
        instruction_profile="reasoner",
    ),
    # Live ListModels probe 2026-07-14 — same knob surface as claude-sonnet-5
    # (thinking/context/effort; no fast; no cyber fixed_param).
    "claude-fable-5": ModelCapability(
        knobs={
            "thinking": KnobSpec(accepted=("false", "true")),
            "context": KnobSpec(accepted=("300k", "1m")),
            "effort": KnobSpec(accepted=("low", "medium", "high", "xhigh", "max")),
        },
        default_variant={
            "thinking": "true",
            "context": "1m",
            "effort": "high",
        },
        instruction_profile="reasoner",
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
        instruction_profile="reasoner",
    ),
    # GPT-5.6 family (GA 2026-07-09) — knobs mirrored from gpt-5.5 until live
    # ListModels widens accepted sets. Prefer explicit tier ids; bare gpt-5.6 → Sol.
    "gpt-5.6-sol": ModelCapability(
        knobs={
            "context": KnobSpec(accepted=("272k", "1m")),
            "reasoning": KnobSpec(
                accepted=("none", "low", "medium", "high", "extra-high")
            ),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        default_variant={"context": "1m", "reasoning": "medium", "fast": "false"},
        instruction_profile="reasoner",
    ),
    "gpt-5.6-terra": ModelCapability(
        knobs={
            "context": KnobSpec(accepted=("272k", "1m")),
            "reasoning": KnobSpec(
                accepted=("none", "low", "medium", "high", "extra-high")
            ),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        default_variant={"context": "1m", "reasoning": "medium", "fast": "false"},
        instruction_profile="reasoner",
    ),
    "gpt-5.6-luna": ModelCapability(
        knobs={
            "context": KnobSpec(accepted=("272k", "1m")),
            "reasoning": KnobSpec(
                accepted=("none", "low", "medium", "high", "extra-high")
            ),
            "fast": KnobSpec(accepted=("false", "true")),
        },
        default_variant={"context": "1m", "reasoning": "medium", "fast": "false"},
        instruction_profile="reasoner",
    ),
    # Cursor Grok 4.5 — live ListModels 2026-07-14: effort + fast only
    # (no thinking/context knobs). Catalog default is fast=true; override via
    # model_knobs when non-fast is wanted.
    "grok-4.5": ModelCapability(
        knobs={
            "effort": KnobSpec(accepted=("low", "medium", "high"), default="high"),
            "fast": KnobSpec(accepted=("false", "true"), default="true"),
        },
        default_variant={
            "effort": "high",
            "fast": "true",
        },
        instruction_profile="reasoner",
    ),
    "gemini-3.5-flash": ModelCapability(
        knobs={},
        default_variant={},
        instruction_profile="mechanical",
    ),
    # GA 2026-07-21 — successor to gemini-3.5-flash; same cursor-sdk knob surface.
    "gemini-3.6-flash": ModelCapability(
        knobs={},
        default_variant={},
        instruction_profile="mechanical",
    ),
}


def canonical_cursor_bare_id(model: str) -> str:
    """Normalize a bare or ``cursor/``-prefixed id to lowercase bare wire id."""
    from model_id import ModelId

    parsed = ModelId.parse(model)
    if parsed.provider is not None and parsed.provider != "cursor":
        raise ValueError(
            f"model {parsed.original!r} has provider {parsed.provider!r}; "
            f"cursor canonicalization accepts bare ids or 'cursor/' prefix only"
        )
    return parsed.api_model_id.lower()


def is_cursor_model_denied(model: str) -> bool:
    """True when *model* canonicalizes to a member of ``CURSOR_DENIED_MODELS``."""
    bare = canonical_cursor_bare_id(model)
    return bare in {denied.lower() for denied in CURSOR_DENIED_MODELS}


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
