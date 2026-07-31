"""Additive-field tests for the handoff executor_recommendation object."""

from __future__ import annotations

from systems.frontier_consult.handoff_response import (
    build_executor_recommendation_field,
    build_recommended_executor,
)


def test_field_is_additive_and_namespaced() -> None:
    field = build_executor_recommendation_field(
        handoff_contract="light-bounded",
        target_surface="claude-cursor",
        target_model="claude-opus-4-8",
    )
    assert set(field) == {"executor_recommendation"}
    obj = field["executor_recommendation"]
    assert obj["status"] == "recommended"
    assert obj["schema_version"] == "1"


def test_legacy_field_untouched_by_recommendation_builder() -> None:
    # The legacy coarse builder is independent of the new object.
    legacy = build_recommended_executor(
        handoff_contract="implement", packet_text=""
    )
    assert "recommended_executor" in legacy
    field = build_executor_recommendation_field(
        handoff_contract="implement",
        target_surface="claude-cursor",
        target_model="claude-opus-4-8",
    )
    # Additive object reports none for implement; legacy still composer.
    assert field["executor_recommendation"]["status"] == "none"
    assert legacy["recommended_executor"] == "composer"
