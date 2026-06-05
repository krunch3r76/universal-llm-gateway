"""Tests for consensus panel dispatch helpers."""

from __future__ import annotations

from agent_seat.panel_dispatch import (
    MIN_PANEL_PROVIDER_FAMILIES,
    PanelMemberSpec,
    admit_panel_plan,
    build_panel_assert_attributes,
    build_team_dispatch_body,
    effective_model_for_member,
    panel_provider_families,
    resolve_panel_members,
    validate_panel_assert_attributes,
)


def test_resolve_panel_members_default_two_roles() -> None:
    members = resolve_panel_members()
    roles = [m.role for m in members]
    assert roles == ["skeptic", "reviewer"]
    assert members[1].model == "openai/gpt-5.5"


def test_resolve_panel_members_optional_synthesizer() -> None:
    members = resolve_panel_members(include_synthesizer=True)
    assert [m.role for m in members] == ["skeptic", "reviewer", "synthesizer"]


def test_panel_provider_families_distinct_providers() -> None:
    models = {
        "skeptic": "xai/grok-4.3",
        "reviewer": "openai/gpt-5.5",
    }
    families = panel_provider_families(models)
    assert len(families) >= MIN_PANEL_PROVIDER_FAMILIES
    assert "Grok" in families
    assert "GPT" in families


def test_admit_panel_plan_rejects_non_panel() -> None:
    result = admit_panel_plan(disposition="steelman-only")
    assert isinstance(result, dict)
    assert result["error"]["code"] == "validation_error"


def test_admit_panel_plan_accepts_panel() -> None:
    result = admit_panel_plan(disposition="panel")
    assert not isinstance(result, dict)
    assert len(result.members) == 2


def test_build_team_dispatch_body_shape() -> None:
    spec = PanelMemberSpec(role="reviewer", model="openai/gpt-5.5")
    body = build_team_dispatch_body(
        spec=spec,
        messages=[{"role": "user", "content": "test"}],
        dispatch_thread_id="cursor-2026-06-02-panel",
        caller_agent="claude-cursor",
    )
    assert body["op"] == "generate"
    assert body["role"] == "reviewer"
    assert body["model"] == "openai/gpt-5.5"
    assert body["dispatch_thread_id"] == "cursor-2026-06-02-panel"
    assert body["caller_agent"] == "claude-cursor"


def test_build_team_dispatch_body_resolves_role_default_model() -> None:
    """Skeptic roster entry has model=None; wire body must still carry explicit model."""
    spec = PanelMemberSpec(role="skeptic", model=None)
    body = build_team_dispatch_body(
        spec=spec,
        messages=[{"role": "user", "content": "test"}],
        dispatch_thread_id="cursor-2026-06-02-panel",
    )
    assert body["role"] == "skeptic"
    assert body["model"] == effective_model_for_member(spec)
    assert str(body["model"]).startswith("xai/")


def test_validate_panel_assert_requires_artifact_and_falsifier() -> None:
    errors = validate_panel_assert_attributes(
        {
            "consensus_disposition": "panel",
            "panel_families": ["Grok", "GPT"],
            "panel_executions": {"skeptic": "eb94f022", "reviewer": "fe7abdb4"},
            "decisive_falsifier": "",
            "panel_adjudication_artifact": "",
        }
    )
    assert any("panel_adjudication_artifact" in e for e in errors)
    assert any("decisive_falsifier" in e for e in errors)


def test_validate_panel_assert_accepts_deprecated_alias() -> None:
    """`lead_adjudication_artifact` (deprecated alias) still satisfies Guard 2."""
    errors = validate_panel_assert_attributes(
        {
            "consensus_disposition": "panel",
            "panel_families": ["Grok", "GPT"],
            "panel_executions": {"skeptic": "eb94f022", "reviewer": "fe7abdb4"},
            "decisive_falsifier": "falsifier text",
            "lead_adjudication_artifact": "cortex:notes/system/threads/1206-lead.md",
        }
    )
    assert not any("adjudication_artifact" in e for e in errors)


def test_build_panel_assert_attributes_menu_d() -> None:
    attrs = build_panel_assert_attributes(
        panel_executions={"skeptic": "eb94f022", "reviewer": "fe7abdb4"},
        decisive_falsifier="lack-of-adjudication-artifact fraction rises",
        panel_adjudication_artifact="cortex:notes/system/threads/1206-panel-adjudication-artifact.md",
        member_models={
            "skeptic": "xai/grok-4.3",
            "reviewer": "openai/gpt-5.5",
        },
    )
    assert attrs["consensus_disposition"] == "panel"
    assert len(attrs["panel_families"]) >= 2
    assert attrs["panel_adjudication_artifact"].startswith("cortex:")
    assert "lead_adjudication_artifact" not in attrs


def test_effective_model_uses_role_default_when_omitted() -> None:
    spec = PanelMemberSpec(role="skeptic", model=None)
    model = effective_model_for_member(spec)
    assert model.startswith("xai/")
