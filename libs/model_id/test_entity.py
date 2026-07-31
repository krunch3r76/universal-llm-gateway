"""Tests for Cortex model entity normalization."""

from __future__ import annotations

from model_id import ModelId, canonical_model_entity_id


def test_provider_prefix_is_removed_for_model_entity() -> None:
    assert canonical_model_entity_id("google/gemini-2.5-pro") == "model:gemini-2.5-pro"


def test_openrouter_routing_prefix_is_removed_for_model_entity() -> None:
    assert (
        canonical_model_entity_id("openrouter/google/gemini-2.5-pro")
        == "model:gemini-2.5-pro"
    )


def test_distinct_gemini_versions_are_not_collapsed() -> None:
    assert (
        canonical_model_entity_id("google/gemini-3.1-pro-preview")
        == "model:gemini-3.1-pro-preview"
    )


def test_model_id_entity_id_property_matches_free_function() -> None:
    """``ModelId.entity_id`` is a discoverable wrapper around
    ``canonical_model_entity_id`` so callers with a parsed instance in
    hand don't have to know the free-function exists."""
    parsed = ModelId.parse("anthropic/claude-opus-4-7")
    assert parsed.entity_id == "model:claude-opus-4-7"
    assert parsed.entity_id == canonical_model_entity_id(parsed)


def test_model_id_entity_id_strips_openrouter_routing_layer() -> None:
    parsed = ModelId.parse("openrouter/openai/gpt-5.4")
    assert parsed.entity_id == "model:gpt-5.4"
