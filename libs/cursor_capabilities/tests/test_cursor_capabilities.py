"""Tests for cursor model capability cards."""

from __future__ import annotations

import pytest
from cursor_capabilities import (
    CURSOR_MODEL_CAPABILITIES,
    ModelCapability,
    effort_knob_name,
    suggest_effort_knobs,
    to_model_card_dict,
)

_GOVERNED_INSTRUCTION_PROFILES: dict[str, str] = {
    "composer-2.5": "mechanical",
    "gemini-3.5-flash": "mechanical",
    "gemini-3.6-flash": "mechanical",
    "claude-opus-5": "reasoner",
    "claude-opus-4-8": "reasoner",
    "claude-sonnet-4-6": "reasoner",
    "claude-sonnet-5": "reasoner",
    "gpt-5.5": "reasoner",
    "grok-4.6": "reasoner",
}


def test_model_capability_default_instruction_profile_is_mechanical() -> None:
    cap = ModelCapability(knobs={}, default_variant={})
    assert cap.instruction_profile == "mechanical"


def test_governed_rows_carry_instruction_profile_classifications() -> None:
    assert len(CURSOR_MODEL_CAPABILITIES) == 13
    for model_id, expected in _GOVERNED_INSTRUCTION_PROFILES.items():
        assert CURSOR_MODEL_CAPABILITIES[model_id].instruction_profile == expected


def test_model_capability_frozen_round_trip() -> None:
    cap = CURSOR_MODEL_CAPABILITIES["composer-2.5"]
    round_trip = ModelCapability(
        knobs=cap.knobs,
        default_variant=cap.default_variant,
        fixed_params=cap.fixed_params,
        instruction_profile=cap.instruction_profile,
    )
    assert round_trip == cap
    with pytest.raises(AttributeError):
        cap.instruction_profile = "reasoner"  # type: ignore[misc]


def test_opus_fixed_params_carries_cyber_outside_default_variant() -> None:
    cap = CURSOR_MODEL_CAPABILITIES["claude-opus-4-8"]
    assert cap.fixed_params == {"cyber": "false"}
    assert "cyber" not in cap.default_variant


def test_to_model_card_dict_projects_knobs_and_fixed_params() -> None:
    cap = CURSOR_MODEL_CAPABILITIES["claude-opus-4-8"]
    card = to_model_card_dict(cap)
    assert set(card) == {"knobs", "fixed_params", "instruction_profile"}
    assert card["fixed_params"] == {"cyber": "false"}
    assert card["instruction_profile"] == "reasoner"
    assert "effort" in card["knobs"]
    assert card["knobs"]["effort"]["accepted"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]


def test_effort_knob_name_prefers_effort_then_reasoning() -> None:
    assert effort_knob_name("claude-opus-5") == "effort"
    assert effort_knob_name("gpt-5.6-sol") == "reasoning"
    assert effort_knob_name("composer-2.5") is None
    assert effort_knob_name("gemini-3.6-flash") is None


def test_suggest_effort_knobs_accepted_and_empty() -> None:
    assert suggest_effort_knobs("claude-opus-5", "low") == {"effort": "low"}
    assert suggest_effort_knobs("gpt-5.6-sol", "low") == {"reasoning": "low"}
    assert suggest_effort_knobs("grok-4.6", "max") == {}
    assert suggest_effort_knobs("composer-2.5", "low") == {}
