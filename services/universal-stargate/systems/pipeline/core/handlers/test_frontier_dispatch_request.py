"""Contract tests for frontier_dispatch reasoning-effort translation.

``translate_reasoning_effort`` is now a thin spec-reader delegating to the
``capability_dispatch`` ModelWrapper hierarchy (the static budget/adaptive maps
moved into the libs registry). These cases lock the provider-native shapes the
spec-reader must reproduce.
"""

from __future__ import annotations

import pytest

from systems.pipeline.core.handlers.frontier_dispatch.request import (
    translate_reasoning_effort,
)

# Adaptive-capable Anthropic families (mirrors the registry
# ``_ANTHROPIC_ADAPTIVE_FAMILIES``). Kept as a local fixture so the contract is
# asserted against the spec-reader, not against a registry internal.
_ADAPTIVE_MODEL_SUFFIXES = (
    "claude-mythos-preview",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
)
# Budget-mode token map (mirrors the registry ``_REASONING_BUDGET_MAP``).
_BUDGET_TOKENS = {"low": 2048, "medium": 8192, "high": 24000}
_ADAPTIVE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("model_suffix", _ADAPTIVE_MODEL_SUFFIXES)
@pytest.mark.parametrize("effort", _ADAPTIVE_EFFORTS)
def test_adaptive_anthropic_models_always_translate_to_adaptive(
    model_suffix: str, effort: str
) -> None:
    """Adaptive-capable Anthropic models use {type: adaptive} for every effort."""
    model = f"anthropic/{model_suffix}"
    assert translate_reasoning_effort(effort, "anthropic", model=model) == {
        "type": "adaptive"
    }


def test_opus_4_8_high_and_xhigh_both_adaptive() -> None:
    """Regression: opus-4-8 off the allowlist gave 400 (high) / no-thinking (xhigh)."""
    model = "anthropic/claude-opus-4-8"
    assert translate_reasoning_effort("high", "anthropic", model=model) == {
        "type": "adaptive"
    }
    assert translate_reasoning_effort("xhigh", "anthropic", model=model) == {
        "type": "adaptive"
    }


@pytest.mark.parametrize(
    "effort,budget",
    [(e, _BUDGET_TOKENS[e]) for e in _BUDGET_TOKENS],
)
def test_legacy_anthropic_models_use_budget_mode(effort: str, budget: int) -> None:
    """Non-adaptive Anthropic models map low/medium/high to enabled+budget_tokens."""
    model = "anthropic/claude-sonnet-4-5"
    assert translate_reasoning_effort(effort, "anthropic", model=model) == {
        "type": "enabled",
        "budget_tokens": budget,
    }


def test_legacy_anthropic_xhigh_skips_thinking_config() -> None:
    model = "anthropic/claude-sonnet-4-5"
    assert translate_reasoning_effort("xhigh", "anthropic", model=model) is None
