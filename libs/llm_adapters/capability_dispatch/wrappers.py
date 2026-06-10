"""ModelWrapper hierarchy — the native-field translation MECHANISM (thread 1234).

Keyed by ``api_surface``: base + Anthropic / OpenAIResponses /
OpenAIChatCompletions / GoogleGenerateContent. A wrapper INSTANCE is hydrated
per model from its ``CapabilityDispatch`` (the DATA); the SUBCLASS owns the
translation mechanism (subsuming the four ``build_frontier_request`` knob
bodies). Per G6 + G12 the reasoning ``value_kind`` drives a typed setter in the
subclass — never path-substitution.

The single max-output resolution math lives in the base (shared across
surfaces); only the cross-knob budget extraction and the reasoning translation
differ per surface.
"""

from __future__ import annotations

from typing import Any

from .registry import (
    SURFACE_ANTHROPIC,
    SURFACE_GOOGLE,
    SURFACE_OPENAI_CHAT_COMPLETIONS,
    SURFACE_OPENAI_RESPONSES,
    VALID_REASONING_EFFORTS,
    openai_supports_reasoning_effort,
    resolve,
    xai_supports_reasoning_effort,
)
from .types import (
    CapabilityDispatch,
    KnobViolation,
    ProtocolError,
)

MaxOutputDecision = str  # "explicit" | "default" | "floor_bump" | "ceiling_clamp"


class ModelWrapper:
    """Base wrapper — owns the shared max-output resolution math."""

    api_surface: str = "base"

    def __init__(self, dispatch: CapabilityDispatch) -> None:
        self.dispatch = dispatch

    # -- max output (shared mechanism) ---------------------------------------
    def reasoning_budget(self, thinking_config: dict[str, Any] | None) -> int | None:
        """Extract the cross-knob reasoning budget. Only Anthropic enabled-mode."""
        return None

    def resolve_max_output(
        self, requested: int | None, thinking_config: dict[str, Any] | None = None
    ) -> tuple[int, MaxOutputDecision]:
        """Resolve the effective max-output value + the G2 decision label.

        Order: cross-knob bump (``> reasoning.budget``) → default (when unset)
        → floor-bump → ceiling-clamp/reject. Reproduces the OLD per-stack
        resolution exactly.
        """
        spec = self.dispatch.max_output
        budget = self.reasoning_budget(thinking_config)
        base = requested
        decision: MaxOutputDecision = "explicit" if requested is not None else "default"
        if budget is not None and (base is None or base <= budget):
            base = budget * 2
            decision = "explicit"
        if base is None:
            resolved = spec.default
            decision = "default"
        else:
            resolved = base
        if spec.floor is not None and resolved < spec.floor:
            resolved = spec.floor
            decision = "floor_bump"
        if spec.ceiling is not None and resolved > spec.ceiling:
            if spec.over_ceiling == "reject":
                raise ProtocolError(
                    [
                        KnobViolation(
                            knob="max_output",
                            reject_code="unsupported_by_model",
                            message=(
                                f"requested {resolved} exceeds ceiling "
                                f"{spec.ceiling} (over_ceiling=reject)"
                            ),
                        )
                    ]
                )
            resolved = spec.ceiling
            decision = "ceiling_clamp"
        return resolved, decision

    # -- reasoning (subclass mechanism) --------------------------------------
    def translate_reasoning(self, effort: str) -> dict[str, Any] | None:
        """Map an effort string to the provider-native thinking object."""
        raise NotImplementedError

    def supports_reasoning_effort(self, model: str) -> bool:
        """Whether this model accepts a reasoning-effort knob at all."""
        return True

    @staticmethod
    def _normalize_effort(effort: str) -> str:
        normalized = effort.strip().lower()
        if normalized not in VALID_REASONING_EFFORTS:
            raise ValueError(
                f"reasoning_effort={effort!r} must be one of: "
                f"{', '.join(sorted(VALID_REASONING_EFFORTS))}"
            )
        return normalized

    # -- G9 reject-loudly -----------------------------------------------------
    def reject_unsupported_knobs(
        self, model: str, *, reasoning_effort: str | None = None
    ) -> None:
        """Collect-all-violations G9 gate. Raises ProtocolError if any knob is unsupported."""
        violations: list[KnobViolation] = []
        if reasoning_effort and not self.supports_reasoning_effort(model):
            violations.append(
                KnobViolation(
                    knob="reasoning.effort",
                    reject_code="unsupported_by_model",
                    message=(
                        f"model={model!r} on surface={self.api_surface!r} "
                        f"does not accept reasoning.effort"
                    ),
                )
            )
        elif reasoning_effort:
            reasoning = self.dispatch.reasoning
            if reasoning is not None:
                normalized = reasoning_effort.strip().lower()
                if normalized not in reasoning.accepted_values:
                    valid = ", ".join(sorted(reasoning.accepted_values))
                    violations.append(
                        KnobViolation(
                            knob="reasoning.effort",
                            reject_code="unsupported_by_model",
                            message=(
                                f"reasoning_effort={reasoning_effort!r} not in "
                                f"accepted_values for model={model!r}; valid: {valid}"
                            ),
                        )
                    )
        if violations:
            raise ProtocolError(violations)


class AnthropicWrapper(ModelWrapper):
    api_surface = SURFACE_ANTHROPIC

    def reasoning_budget(self, thinking_config: dict[str, Any] | None) -> int | None:
        if not isinstance(thinking_config, dict):
            return None
        if thinking_config.get("type") != "enabled":
            return None
        budget = thinking_config.get("budget_tokens")
        return budget if isinstance(budget, int) and budget >= 1 else None

    def translate_reasoning(self, effort: str) -> dict[str, Any] | None:
        self._normalize_effort(effort)
        reasoning = self.dispatch.reasoning
        if reasoning is None:
            return None
        if reasoning.value_kind == "adaptive":
            return {"type": "adaptive"}
        normalized = effort.strip().lower()
        budget = (reasoning.budget_map or {}).get(normalized)
        if budget is None:
            return None
        return {"type": "enabled", "budget_tokens": budget}


class _EffortStringWrapper(ModelWrapper):
    """Responses / Google / ChatCompletions share the effort-string mechanism."""

    def translate_reasoning(self, effort: str) -> dict[str, Any] | None:
        normalized = self._normalize_effort(effort)
        return {"effort": normalized}


class OpenAIResponsesWrapper(_EffortStringWrapper):
    api_surface = SURFACE_OPENAI_RESPONSES

    def supports_reasoning_effort(self, model: str) -> bool:
        return openai_supports_reasoning_effort(model) or xai_supports_reasoning_effort(
            model
        )


class OpenAIChatCompletionsWrapper(_EffortStringWrapper):
    api_surface = SURFACE_OPENAI_CHAT_COMPLETIONS

    def supports_reasoning_effort(self, model: str) -> bool:
        return openai_supports_reasoning_effort(model)


class GoogleGenerateContentWrapper(_EffortStringWrapper):
    api_surface = SURFACE_GOOGLE


_SURFACE_WRAPPERS: dict[str, type[ModelWrapper]] = {
    SURFACE_ANTHROPIC: AnthropicWrapper,
    SURFACE_OPENAI_RESPONSES: OpenAIResponsesWrapper,
    SURFACE_OPENAI_CHAT_COMPLETIONS: OpenAIChatCompletionsWrapper,
    SURFACE_GOOGLE: GoogleGenerateContentWrapper,
}


def wrapper_for(model: str) -> ModelWrapper:
    """Hydrate the surface-specific ``ModelWrapper`` for a cloud model.

    G13 ``CatalogMissError`` propagates from ``registry.resolve`` for a
    provider-uninferable model.
    """
    dispatch = resolve(model)
    cls = _SURFACE_WRAPPERS[dispatch.api_surface]
    return cls(dispatch)
