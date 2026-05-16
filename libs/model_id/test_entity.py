"""Tests for Cortex model entity normalization."""

from __future__ import annotations

from model_id import canonical_model_entity_id


def test_provider_prefix_is_removed_for_model_entity() -> None:
    assert (
        canonical_model_entity_id("google/gemini-2.5-pro")
        == "model:gemini-2.5-pro"
    )


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
