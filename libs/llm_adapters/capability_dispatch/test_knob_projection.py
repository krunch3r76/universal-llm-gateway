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


def test_project_knob_resolution_includes_max_output_block() -> None:
    result = project_knob_resolution(
        resolved_model="openai/gpt-5.5",
        requested_effort="high",
        requested_max_output=4096,
    )
    max_output = result["max_output"]
    assert set(max_output) == {
        "requested",
        "resolved",
        "decision",
        "floor",
        "ceiling",
    }
    assert max_output["requested"] == 4096
    assert max_output["resolved"] == 16384
    assert max_output["decision"] == "floor_bump"


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


def test_project_knob_resolution_anthropic_token_budget_unmapped_rejects() -> None:
    result = project_knob_resolution(
        resolved_model="anthropic/claude-sonnet-4-5",
        requested_effort="minimal",
    )
    assert result["rejected"] is True
    assert result["reject_kind"] == "protocol_error"
    assert result["violations"]
    assert any(v["knob"] == "reasoning.effort" for v in result["violations"])
    assert any("valid:" in v["message"] for v in result["violations"])
    assert "status" not in result


def test_project_knob_resolution_xai_defaulted() -> None:
    result = project_knob_resolution(
        resolved_model="xai/grok-4.6",
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
        reasoning_effort="high",
    ).resolved_event_fields()
    assert minimal_fields["reasoning_native"] == {
        "type": "enabled",
        "budget_tokens": 24000,
    }


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


def test_to_model_card_dict_cloud_projection() -> None:
    from llm_adapters.capability_dispatch.registry import resolve
    from llm_adapters.capability_dispatch.serialization import to_model_card_dict

    card = to_model_card_dict(resolve("anthropic/claude-opus-4-8"))
    assert card["api_surface"]
    assert isinstance(card["knobs"], dict)
    assert card["fixed_params"] == {}


def test_project_knob_resolution_perplexity_sonar_not_catalog_miss() -> None:
    """Perplexity surface row closes catalog_miss; absent effort is previewable."""
    result = project_knob_resolution(
        resolved_model="openrouter/perplexity/sonar-deep-research",
        requested_effort=None,
    )
    assert result.get("rejected") is not True
    assert "no dispatch surface" not in str(result).lower()
