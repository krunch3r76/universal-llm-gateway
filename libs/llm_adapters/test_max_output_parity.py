"""G8 parity harness — per-model dispatch resolution (thread 1234 / 1255 build).

This harness is the no-bc completion gate for the per-model ModelDispatchSpec
build. It captures the resolved ``{max_output, reasoning/thinking}`` values
produced by the **OLD** code path (baseline-on-OLD, Fence A) and freezes them in
``_max_output_parity_baseline.json``. The NEW registry+boundary path must
reproduce the identical golden table (Fences C/D).

Coverage (per the build packet <task_guidance> "G8 (parity harness)"):
  - all THREE Anthropic-calling stacks' ``max_tokens`` output (F / CP / WB)
  - Responses floor-bump (sub-16384 bumped up)
  - Anthropic ceiling-clamp (over-ceiling clamped)
  - cross-knob ``max_output > reasoning.budget`` auto-bump
  - reasoning-effort → provider-native ``thinking`` translation + effort-support
    predicates (the reasoning half of the deletion set)

Measurement seam (``_resolve_*``): the ONLY thing that changes between fences is
which mechanism the seam calls. The golden table (the parity *contract*) is
frozen at Fence A and never edited. At Fence A the seam calls the OLD production
resolution; at Fence C it is repointed at the NEW registry boundary while the
golden table stays fixed — that is the no-bc parity proof.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.offline

from llm_adapters.capability_dispatch import (
    default_reasoning_effort,
    openai_supports_reasoning_effort,
    resolve_dispatch,
    wrapper_for,
    xai_supports_reasoning_effort,
)

_BASELINE_PATH = Path(__file__).with_name("_max_output_parity_baseline.json")

# WB relay is keyed to a single default model (server-side artifact relay).
_WB_DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Requested-max_output scenarios exercising default / floor / ceiling / mid.
_MAX_OUTPUT_SCENARIOS: tuple[int | None, ...] = (None, 1000, 50000, 200000)

# (api_model_id, thinking-config) matrix for the Anthropic frontier stack. The
# thinking variants exercise the cross-knob bump (enabled budget) and the
# adaptive (no-bump) path.
_ANTHROPIC_FRONTIER_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",  # ceiling 128000
    "claude-sonnet-4-6",  # ceiling 64000
    "claude-3-5-sonnet",  # ceiling 8192
    "claude-fable-5",  # ceiling 128000 (newly carded — F1 root cause)
    "claude-mythos-5",  # ceiling 128000 (newly carded)
    "claude-mythos-preview",  # ceiling 128000 (newly carded)
)
_THINKING_VARIANTS: dict[str, dict[str, Any] | None] = {
    "none": None,
    "enabled_b24000": {"type": "enabled", "budget_tokens": 24000},
    "adaptive": {"type": "adaptive"},
}

_RESPONSES_MODELS: tuple[str, ...] = ("gpt-5.5", "grok-4.6")
_GOOGLE_MODELS: tuple[str, ...] = ("gemini-3-pro",)
_CLOUD_PROXY_MODELS: tuple[str, ...] = ("claude-opus-4-8", "claude-sonnet-4-6")


# --------------------------------------------------------------------------- #
# Resolution seam — NEW registry/boundary path (Fence C repoint). The seam now
# calls the single ``resolve_dispatch`` boundary; the frozen golden table below
# is unchanged from Fence A. Parity on this path (OLD symbols still present) is
# the Fence C gate; the same harness re-runs post-deletion as the Fence D gate.
# All three cloud stacks (F / CP / WB) resolve through the ONE libs registry.
# --------------------------------------------------------------------------- #


def _resolve_anthropic_frontier(model: str, requested: int | None, think: str) -> int:
    return resolve_dispatch(
        model, requested_max_output=requested, thinking=_THINKING_VARIANTS[think]
    ).max_output.resolved


def _resolve_responses_frontier(model: str, requested: int | None) -> int:
    return resolve_dispatch(model, requested_max_output=requested).max_output.resolved


def _resolve_google_frontier(model: str, requested: int | None) -> int:
    return resolve_dispatch(model, requested_max_output=requested).max_output.resolved


def _resolve_cloud_proxy(model: str, requested: int | None) -> int:
    """CP resolves through the same libs registry (independent resolution site).

    CP is an out-of-claim independent resolver (A8): it migrates off the deleted
    static helper to the NEW registry but stays a separate resolution call. With
    an empty live ``/models`` cache the resolution reduces exactly to the
    registry's per-model ceiling clamp.
    """
    return resolve_dispatch(model, requested_max_output=requested).max_output.resolved


def _resolve_workbench(requested: int | None) -> int:
    """WB relay resolves through the registry (fixed default model)."""
    return resolve_dispatch(
        _WB_DEFAULT_MODEL, requested_max_output=requested
    ).max_output.resolved


# --------------------------------------------------------------------------- #
# Golden-table construction
# --------------------------------------------------------------------------- #


def _build_max_output_table() -> dict[str, int]:
    table: dict[str, int] = {}
    for model in _ANTHROPIC_FRONTIER_MODELS:
        for think in _THINKING_VARIANTS:
            for req in _MAX_OUTPUT_SCENARIOS:
                key = f"F.anthropic|{model}|think={think}|req={req}"
                table[key] = _resolve_anthropic_frontier(model, req, think)
    for model in _RESPONSES_MODELS:
        for req in _MAX_OUTPUT_SCENARIOS:
            table[f"F.responses|{model}|req={req}"] = _resolve_responses_frontier(
                model, req
            )
    for model in _GOOGLE_MODELS:
        for req in _MAX_OUTPUT_SCENARIOS:
            table[f"F.google|{model}|req={req}"] = _resolve_google_frontier(model, req)
    for model in _CLOUD_PROXY_MODELS:
        for req in _MAX_OUTPUT_SCENARIOS:
            table[f"CP.anthropic|{model}|req={req}"] = _resolve_cloud_proxy(model, req)
    for req in _MAX_OUTPUT_SCENARIOS:
        table[f"WB.anthropic|{_WB_DEFAULT_MODEL}|req={req}"] = _resolve_workbench(req)
    return table


def _build_reasoning_table() -> dict[str, Any]:
    """Reasoning-half parity: effort→thinking translation + support predicates.

    Repointed at the NEW registry mechanism (Fence C): per-surface
    ``ModelWrapper.translate_reasoning`` + the registry's support predicates and
    implicit-default resolver. Golden table stays frozen from Fence A.
    """
    table: dict[str, Any] = {}
    efforts = ("low", "medium", "high", "xhigh")
    translate_matrix = (
        ("anthropic", "anthropic/claude-opus-4-8"),  # adaptive
        ("anthropic", "anthropic/claude-sonnet-4-5"),  # budget-mode
        ("openai", "openai/gpt-5.5"),
        ("xai", "xai/grok-4.6"),
        ("google", "google/gemini-3-pro"),
    )
    for provider, model in translate_matrix:
        wrapper = wrapper_for(model)
        for effort in efforts:
            key = f"translate|{provider}|{model}|{effort}"
            table[key] = wrapper.translate_reasoning(effort)
    for model in ("xai/grok-4.6", "openai/gpt-5.5", "anthropic/claude-opus-4-8"):
        table[f"default_effort|{model}"] = default_reasoning_effort(model)
    for model in ("grok-4.6", "grok-4.20-0309-non-reasoning", "grok-3-mini"):
        table[f"xai_supports|{model}"] = xai_supports_reasoning_effort(model)
    for model in ("gpt-5.5", "o3", "gpt-4o", "o4-mini"):
        table[f"openai_supports|{model}"] = openai_supports_reasoning_effort(model)
    return table


def _build_full_table() -> dict[str, Any]:
    return {
        "max_output": _build_max_output_table(),
        "reasoning": _build_reasoning_table(),
    }


def _load_or_capture_baseline() -> dict[str, Any]:
    """Load the frozen baseline; capture it on first run (Fence A)."""
    actual = _build_full_table()
    if not _BASELINE_PATH.exists():
        _BASELINE_PATH.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
    return actual


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_max_output_parity_matches_baseline() -> None:
    """Production max_output resolution == frozen golden baseline (G8)."""
    actual = _load_or_capture_baseline()
    golden = json.loads(_BASELINE_PATH.read_text())
    mismatches = {
        k: (golden["max_output"].get(k), v)
        for k, v in actual["max_output"].items()
        if golden["max_output"].get(k) != v
    }
    assert not mismatches, f"max_output parity drift: {mismatches}"


def test_reasoning_parity_matches_baseline() -> None:
    """Production reasoning resolution == frozen golden baseline (G8)."""
    actual = _load_or_capture_baseline()
    golden = json.loads(_BASELINE_PATH.read_text())
    mismatches = {
        k: (golden["reasoning"].get(k), v)
        for k, v in actual["reasoning"].items()
        if golden["reasoning"].get(k) != v
    }
    assert not mismatches, f"reasoning parity drift: {mismatches}"


@pytest.mark.parametrize(
    ("model", "requested", "think", "expected"),
    [
        # Anthropic ceiling-clamp: 200000 over the per-model ceiling.
        ("claude-opus-4-8", 200000, "none", 128000),
        ("claude-sonnet-4-6", 200000, "none", 64000),
        # No request → model-max default.
        ("claude-opus-4-8", None, "none", 128000),
        # Cross-knob bump: enabled budget 24000, request 1000 → 48000 (2×budget),
        # then clamped to ceiling.
        ("claude-opus-4-8", 1000, "enabled_b24000", 48000),
        ("claude-sonnet-4-6", 1000, "enabled_b24000", 48000),
        # Under-ceiling passthrough.
        ("claude-opus-4-8", 50000, "none", 50000),
    ],
)
def test_anthropic_frontier_resolution_cases(
    model: str, requested: int | None, think: str, expected: int
) -> None:
    assert _resolve_anthropic_frontier(model, requested, think) == expected


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (None, 131072),  # default
        (1000, 16384),  # floor-bump
        (50000, 50000),  # passthrough
        (200000, 200000),  # no ceiling on Responses
    ],
)
def test_responses_floor_bump_cases(requested: int | None, expected: int) -> None:
    assert _resolve_responses_frontier("gpt-5.5", requested) == expected
