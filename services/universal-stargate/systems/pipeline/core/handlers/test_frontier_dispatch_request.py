"""Contract tests for frontier_dispatch reasoning-effort translation."""

from __future__ import annotations

import pytest

from systems.pipeline.core.handlers.frontier_dispatch_request import (
    _ANTHROPIC_ADAPTIVE_MODELS,
    _REASONING_EFFORT_BUDGET_TOKENS,
    translate_reasoning_effort,
)

_ADAPTIVE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@pytest.mark.parametrize("model_suffix", _ANTHROPIC_ADAPTIVE_MODELS)
@pytest.mark.parametrize("effort", _ADAPTIVE_EFFORTS)
def test_adaptive_anthropic_models_always_translate_to_adaptive(
    model_suffix: str, effort: str
) -> None:
    """Every adaptive-capable Anthropic model must use {type: adaptive} for all efforts."""
    model = f"anthropic/{model_suffix}"
    assert translate_reasoning_effort(effort, "anthropic", model=model) == {
        "type": "adaptive"
    }


def test_opus_4_8_high_and_xhigh_both_adaptive() -> None:
    """Regression: opus-4-8 omitted from allowlist caused 400 (high) or silent no-thinking (xhigh)."""
    model = "anthropic/claude-opus-4-8"
    assert translate_reasoning_effort("high", "anthropic", model=model) == {
        "type": "adaptive"
    }
    assert translate_reasoning_effort("xhigh", "anthropic", model=model) == {
        "type": "adaptive"
    }


@pytest.mark.parametrize(
    "effort,budget",
    [(e, _REASONING_EFFORT_BUDGET_TOKENS[e]) for e in _REASONING_EFFORT_BUDGET_TOKENS],
)
def test_legacy_anthropic_models_use_budget_mode(effort: str, budget: int) -> None:
    """Non-adaptive Anthropic models still map low/medium/high to enabled+budget_tokens."""
    model = "anthropic/claude-sonnet-4-5"
    assert translate_reasoning_effort(effort, "anthropic", model=model) == {
        "type": "enabled",
        "budget_tokens": budget,
    }


def test_legacy_anthropic_xhigh_skips_thinking_config() -> None:
    model = "anthropic/claude-sonnet-4-5"
    assert translate_reasoning_effort("xhigh", "anthropic", model=model) is None
