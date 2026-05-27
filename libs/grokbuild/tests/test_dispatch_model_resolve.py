"""Tier preset model resolution + bad-tier admission for dispatch_op."""

from __future__ import annotations

from typing import Any

import pytest

from grokbuild.constants import (
    _TIER_PRESETS,
    DEFAULT_TIMEOUT_SECONDS,
    default_model_for_tier,
)
from grokbuild.dispatch import _resolve_params, dispatch_op
from grokbuild.runner import _build_argv
from grokbuild.test_support import runner_spec


def test_resolve_params_preset_fields_all_tiers() -> None:
    """All tier presets carry default_model='grok-build' plus the expected
    effort scalars; verifies the _TierPreset field shape did not regress
    when default_model was added alongside reasoning_effort/effort, and
    that _resolve_params copies the effort scalars through correctly."""
    expected = {
        "quick": ("minimal", "low"),
        "balanced": ("medium", "medium"),
        "thorough": ("high", "high"),
        "max": ("xhigh", "max"),
    }
    for tier, (eff_r, eff) in expected.items():
        assert _TIER_PRESETS[tier].default_model == "grok-build"
        assert _TIER_PRESETS[tier].reasoning_effort == eff_r
        assert _TIER_PRESETS[tier].effort == eff

        resolved = _resolve_params(
            tier=tier,
            reasoning_effort=None,
            effort=None,
            timeout_seconds=None,
            check=None,
            max_turns=None,
            best_of_n=None,
            mode="edit",
        )
        assert resolved.reasoning_effort == eff_r
        assert resolved.effort == eff
        assert resolved.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_resolve_params_timeout_explicit_override() -> None:
    resolved = _resolve_params(
        tier="balanced",
        reasoning_effort=None,
        effort=None,
        timeout_seconds=7200,
        check=None,
        max_turns=None,
        best_of_n=None,
        mode="edit",
    )
    assert resolved.timeout_seconds == 7200


def test_resolve_params_timeout_zero_means_unlimited() -> None:
    resolved = _resolve_params(
        tier="balanced",
        reasoning_effort=None,
        effort=None,
        timeout_seconds=0,
        check=None,
        max_turns=None,
        best_of_n=None,
        mode="edit",
    )
    assert resolved.timeout_seconds is None


def test_default_model_for_tier_all_presets() -> None:
    for tier in ("quick", "balanced", "thorough", "max"):
        assert default_model_for_tier(tier) == "grok-build"


def test_dispatch_op_model_none_becomes_tier_default() -> None:
    """Mirrors dispatch_op: model=None → tier preset base id for envelope/sidecar."""
    model: str | None = None
    resolved = _resolve_params(
        tier="balanced",
        reasoning_effort=None,
        effort=None,
        timeout_seconds=None,
        check=None,
        max_turns=None,
        best_of_n=None,
        mode="edit",
    )
    if model is None:
        model = _TIER_PRESETS[resolved.tier].default_model
    assert model == "grok-build"


def test_build_argv_tier_balanced_model_after_resolve() -> None:
    """Tier preset resolves to grok-build; effort flags suppressed for that model."""
    resolved = _resolve_params(
        tier="balanced",
        reasoning_effort=None,
        effort=None,
        timeout_seconds=None,
        check=None,
        max_turns=None,
        best_of_n=None,
        mode="edit",
    )
    spec = runner_spec(
        cwd="/tmp",
        model=_TIER_PRESETS[resolved.tier].default_model,
        tier=resolved.tier,
        reasoning_effort=resolved.reasoning_effort,
        effort=resolved.effort,
    )
    argv = _build_argv(spec)
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "grok-build"
    assert "--reasoning-effort" not in argv
    assert "--effort" not in argv


def test_build_argv_explicit_grok_build_unchanged() -> None:
    spec = runner_spec(cwd="/tmp", model="grok-build", tier="balanced")
    argv = _build_argv(spec)
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "grok-build"


@pytest.mark.asyncio
async def test_dispatch_op_bad_tier_returns_structured_rejection(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """Pre-resolve bad-tier guard returns the structured rejected envelope
    rather than letting _resolve_params raise KeyError (which would land
    as ``dispatch_crashed: KeyError`` in the worker tracker, bypassing the
    uniform envelope contract).

    Verifies:
    - envelope.status == 'rejected'
    - envelope.metadata.reason_code == 'bad_tier'
    - the rejection event was emitted with structured correlation fields
      (per admission-phase payload contract).
    """
    envelope = await dispatch_op(
        cwd="/tmp",
        prompt="x",
        mode="read_only",
        system_context=None,
        model=None,
        session_id=None,
        continue_recent=False,
        output_format="streaming-json",
        timeout_seconds=None,
        tier="bogus",
        reasoning_effort=None,
        effort=None,
        check=None,
        no_subagents=False,
        disable_web_search=False,
        max_turns=None,
        best_of_n=None,
        resume_strict=False,
    )
    assert envelope["status"] == "rejected"
    assert envelope["metadata"]["reason_code"] == "bad_tier"
    assert "bogus" in envelope["metadata"]["reason"]

    rejected = [
        (sig, payload)
        for sig, payload in event_log
        if sig == "mcp.grokbuild.dispatch.rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0][1]["reason_code"] == "bad_tier"
    assert rejected[0][1]["op"] == "build"
    assert rejected[0][1]["cwd"] == "/tmp"


@pytest.mark.asyncio
async def test_dispatch_op_bad_model_returns_structured_rejection(
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    envelope = await dispatch_op(
        cwd="/tmp",
        prompt="x",
        mode="read_only",
        system_context=None,
        model="grok-4.3",
        session_id=None,
        continue_recent=False,
        output_format="streaming-json",
        timeout_seconds=None,
        tier="balanced",
        reasoning_effort=None,
        effort=None,
        check=None,
        no_subagents=False,
        disable_web_search=False,
        max_turns=None,
        best_of_n=None,
        resume_strict=False,
    )
    assert envelope["status"] == "rejected"
    assert envelope["metadata"]["reason_code"] == "bad_model"
    assert "grok-4.3" in envelope["metadata"]["reason"]
