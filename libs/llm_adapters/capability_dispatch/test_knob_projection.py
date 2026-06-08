"""Tests for knob_resolution preview projection and enriched event fields."""

from __future__ import annotations

from llm_adapters.capability_dispatch import project_knob_resolution, resolve_dispatch


def test_project_knob_resolution_openai_mapped() -> None:
    result = project_knob_resolution(
        resolved_model="openai/gpt-5.5",
        requested_effort="high",
    )
    assert result["status"] == "mapped"
    assert result["value_kind"] == "effort_string"
    assert result["reasoning_native"] == {"effort": "high"}
    assert result["parity"] == "not_claimed"


def test_project_knob_resolution_anthropic_adaptive() -> None:
    result = project_knob_resolution(
        resolved_model="anthropic/claude-opus-4-8",
        requested_effort="high",
    )
    assert result["status"] == "mapped"
    assert result["value_kind"] == "adaptive"
    assert result["reasoning_native"] == {"type": "adaptive"}
    assert any("output_config.effort" in note for note in result["notes"])


def test_project_knob_resolution_anthropic_token_budget_mapped() -> None:
    result = project_knob_resolution(
        resolved_model="anthropic/claude-sonnet-4-5",
        requested_effort="high",
    )
    assert result["status"] == "mapped"
    assert result["reasoning_native"] == {
        "type": "enabled",
        "budget_tokens": 24000,
    }


def test_project_knob_resolution_anthropic_no_thinking() -> None:
    result = project_knob_resolution(
        resolved_model="anthropic/claude-sonnet-4-5",
        requested_effort="minimal",
    )
    assert result["status"] == "no_thinking"
    assert result["reasoning_native"] is None


def test_project_knob_resolution_xai_defaulted() -> None:
    result = project_knob_resolution(
        resolved_model="xai/grok-4.3",
        requested_effort=None,
    )
    assert result["status"] == "defaulted"
    assert any("default" in note for note in result["notes"])


def test_project_knob_resolution_g9_reject() -> None:
    result = project_knob_resolution(
        resolved_model="openai/gpt-4o",
        requested_effort="high",
    )
    assert result["rejected"] is True
    assert result["reject_kind"] == "protocol_error"
    assert result["violations"]
    assert "status" not in result


def test_resolved_event_fields_enriched() -> None:
    fields = resolve_dispatch(
        "openai/gpt-5.5",
        reasoning_effort="high",
    ).resolved_event_fields()
    assert fields["reasoning_effort"] == "high"
    assert fields["reasoning_native"] == {"effort": "high"}
    assert fields["reasoning_value_kind"] == "effort_string"

    minimal_fields = resolve_dispatch(
        "anthropic/claude-sonnet-4-5",
        reasoning_effort="minimal",
    ).resolved_event_fields()
    assert minimal_fields["reasoning_native"] is None


def test_adaptive_event_overlay_fields() -> None:
    resolution = resolve_dispatch(
        "anthropic/claude-opus-4-8",
        reasoning_effort="high",
    )
    resolved_fields = dict(resolution.resolved_event_fields())
    reasoning_effort = "high"
    if resolution.reasoning.value_kind == "adaptive" and reasoning_effort:
        resolved_fields["reasoning_output_config_effort"] = reasoning_effort
    assert resolved_fields["reasoning_native"] == {"type": "adaptive"}
    assert resolved_fields["reasoning_value_kind"] == "adaptive"
    assert resolved_fields["reasoning_output_config_effort"] == "high"
