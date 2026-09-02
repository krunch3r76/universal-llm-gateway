"""Tests for consensus panel dispatch helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_seat.panel_dispatch import (
    MIN_PANEL_PROVIDER_FAMILIES,
    PanelMemberSpec,
    admit_panel_plan,
    build_panel_assert_attributes,
    build_panel_poll_summary,
    build_team_dispatch_body,
    effective_model_for_member,
    lint_panel_messages,
    member_dispatch_thread_id,
    panel_identity_labels,
    panel_provider_families,
    panel_result_envelope,
    resolve_panel_members,
    validate_panel_assert_attributes,
    verify_panel_role_model_resolution,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEAM_DISPATCH_YAML = (
    _REPO_ROOT / "pipelines.local/team_dispatch/v1/team-dispatch-v1.yaml"
)


def test_resolve_panel_members_default_two_roles() -> None:
    members = resolve_panel_members()
    roles = [m.role for m in members]
    assert roles == ["skeptic", "reviewer"]
    assert members[1].model == "cursor/gpt-5.6-terra"


def test_resolve_panel_members_optional_synthesizer() -> None:
    members = resolve_panel_members(include_synthesizer=True)
    assert [m.role for m in members] == ["skeptic", "reviewer", "synthesizer"]


def test_panel_identity_labels_distinct_identities() -> None:
    models = {
        "skeptic": "xai/grok-4.6",
        "reviewer": "openai/gpt-5.6-terra",
    }
    labels = panel_identity_labels(models)
    assert len(labels) >= MIN_PANEL_PROVIDER_FAMILIES
    assert "grok-4.6@?" in labels
    assert "gpt-5.6-terra@?" in labels
    assert panel_provider_families(models) == labels


def test_admit_panel_plan_rejects_non_panel() -> None:
    result = admit_panel_plan(disposition="steelman-only")
    assert isinstance(result, dict)
    assert result["error"]["code"] == "validation_error"


def test_admit_panel_plan_accepts_panel() -> None:
    result = admit_panel_plan(disposition="panel")
    assert not isinstance(result, dict)
    assert len(result.members) == 2


def test_admit_panel_plan_member_models_honored_in_identities() -> None:
    """Friction 23301: explicit per-role overrides participate in identity gate."""
    result = admit_panel_plan(
        disposition="panel",
        member_models={
            "skeptic": "xai/grok-4.6",
            "reviewer": "openai/gpt-5.5",
        },
    )
    assert not isinstance(result, dict)
    models = {m.role: effective_model_for_member(m) for m in result.members}
    assert models["skeptic"] == "xai/grok-4.6"
    assert models["reviewer"] == "openai/gpt-5.5"
    labels = panel_identity_labels(models)
    assert set(labels) == {"grok-4.6@?", "gpt-5.5@?"}


def test_admit_panel_plan_same_model_different_rung_passes() -> None:
    """R-PANEL: same grok-4.6 identity at cloud vs cursor effort rung counts distinct."""
    result = admit_panel_plan(
        disposition="panel",
        member_models={
            "skeptic": "xai/grok-4.6",
            "reviewer": "cursor/grok-4.6",
        },
    )
    assert not isinstance(result, dict)
    models = {m.role: effective_model_for_member(m) for m in result.members}
    assert set(panel_identity_labels(models)) == {"grok-4.6@?", "grok-4.6@high"}


def test_admit_panel_plan_same_identity_rejected() -> None:
    """Duplicate consultant identity on both roster roles rejects at Guard 3."""
    result = admit_panel_plan(
        disposition="panel",
        member_models={
            "skeptic": "openai/gpt-5.6-terra",
            "reviewer": "openai/gpt-5.6-terra",
        },
    )
    assert isinstance(result, dict)
    assert result["error"]["code"] == "validation_error"
    assert "distinct consultant identities" in result["error"]["message"]


def test_admit_panel_plan_member_models_unknown_role_rejected() -> None:
    result = admit_panel_plan(
        disposition="panel",
        member_models={"artisan": "xai/grok-4.6"},
    )
    assert isinstance(result, dict)
    assert result["error"]["code"] == "validation_error"
    assert "non-roster roles" in result["error"]["message"]


def test_admit_panel_plan_member_models_disallowed_model_rejected() -> None:
    result = admit_panel_plan(
        disposition="panel",
        member_models={"reviewer": "xai/grok-4.6"},
    )
    assert isinstance(result, dict)
    assert result["error"]["code"] == "validation_error"
    assert "allowed_models" in result["error"]["message"]


def test_build_team_dispatch_body_shape() -> None:
    spec = PanelMemberSpec(role="reviewer", model="openai/gpt-5.6-terra")
    body = build_team_dispatch_body(
        spec=spec,
        dispatch_thread_id="cursor-2026-06-02-panel",
        caller_agent="claude-cursor",
    )
    assert body["op"] == "generate"
    assert body["role"] == "reviewer"
    assert body["model"] == "openai/gpt-5.6-terra"
    assert body["dispatch_thread_id"] == "cursor-2026-06-02-panel"
    assert body["caller_agent"] == "claude-cursor"


def test_build_team_dispatch_body_omits_model_when_role_default() -> None:
    """Skeptic roster entry has model=None; omit model like team_dispatch generate."""
    spec = PanelMemberSpec(role="skeptic", model=None)
    body = build_team_dispatch_body(
        spec=spec,
        dispatch_thread_id="cursor-2026-06-02-panel",
    )
    assert body["role"] == "skeptic"
    assert "model" not in body
    assert effective_model_for_member(spec).startswith("xai/")


def test_build_team_dispatch_body_passes_generate_options() -> None:
    spec = PanelMemberSpec(role="reviewer", model="openai/gpt-5.6-terra")
    body = build_team_dispatch_body(
        spec=spec,
        dispatch_thread_id="cursor-2026-06-02-panel",
        reasoning_effort="high",
        generation_options={"temperature": 0.2},
        max_tool_turns=12,
        transcript_id="cursor-2026-06-05-1200",
        timeout_seconds=120,
    )
    assert body["reasoning_effort"] == "high"
    assert body["generation_options"] == {"temperature": 0.2}
    assert body["max_tool_turns"] == 12
    assert body["transcript_id"] == "cursor-2026-06-05-1200"
    assert body["timeout_seconds"] == 120


def test_validate_panel_assert_requires_artifact_and_falsifier() -> None:
    errors = validate_panel_assert_attributes(
        {
            "consensus_disposition": "panel",
            "panel_families": ["grok-4.6@?", "gpt-5.6-terra@medium"],
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
            "panel_families": ["grok-4.6@?", "gpt-5.6-terra@medium"],
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
            "skeptic": "xai/grok-4.6",
            "reviewer": "openai/gpt-5.6-terra",
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


def test_verify_panel_role_model_resolution_passes_default_roster() -> None:
    errors = verify_panel_role_model_resolution()
    assert errors == [], f"panel role resolution drift: {errors}"


def test_member_dispatch_thread_id_suffix() -> None:
    assert member_dispatch_thread_id("panel-base", "skeptic") == "panel-base:skeptic"


def test_lint_panel_messages_rejects_block_array() -> None:
    err = lint_panel_messages([{"role": "user", "content": [{"type": "text"}]}])
    assert err is not None
    assert err["error"]["code"] == "validation_error"


def test_lint_panel_messages_accepts_string() -> None:
    assert lint_panel_messages([{"role": "user", "content": "ok"}]) is None


def test_panel_result_envelope_submission_plan() -> None:
    plan = admit_panel_plan(disposition="panel")
    assert not isinstance(plan, dict)
    member_models = {"skeptic": "xai/grok-4.6", "reviewer": "openai/gpt-5.6-terra"}
    envelope = panel_result_envelope(
        plan=plan,
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        member_models=member_models,
        submission_plan=[
            {
                "role": "skeptic",
                "model": "xai/grok-4.6",
                "execution_id": "e1",
                "dispatch_key": "base:skeptic",
            },
            {
                "role": "reviewer",
                "model": "openai/gpt-5.6-terra",
                "execution_id": "e2",
                "dispatch_key": "base:reviewer",
            },
        ],
        reasoning_effort="high",
    )
    assert len(envelope["submission_plan"]) == 2
    assert envelope["submission_plan"][0]["dispatch_key"] == "base:skeptic"
    assert "member_knob_resolution" in envelope


def test_build_panel_poll_summary_partial_e6() -> None:
    summary = build_panel_poll_summary(
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        poll_results={
            "skeptic": {"status": "running", "execution_id": "e1"},
            "reviewer": {
                "status": "completed",
                "execution_id": "e2",
                "result": {
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                },
            },
        },
        polled=True,
    )
    assert summary["status"] == "partial"
    assert summary["do_not_resubmit"] is True
    assert summary["in_flight_execution_ids"] == ["e1"]
    assert summary["member_status"] == {
        "skeptic": "running",
        "reviewer": "complete",
    }
    assert summary["tokens_in"] == 100
    assert summary["tokens_out"] == 50


def test_build_panel_poll_summary_complete_e8() -> None:
    summary = build_panel_poll_summary(
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        poll_results={
            "skeptic": {
                "status": "completed",
                "result": {"usage": {"prompt_tokens": 200, "completion_tokens": 30}},
            },
            "reviewer": {
                "status": "completed",
                "result": {"usage": {"prompt_tokens": 150, "completion_tokens": 20}},
            },
        },
        polled=True,
    )
    assert summary["status"] == "complete"
    assert summary["member_status"] == {
        "skeptic": "complete",
        "reviewer": "complete",
    }
    assert summary["tokens_in"] == 350
    assert summary["tokens_out"] == 50
    assert "do_not_resubmit" not in summary or summary.get("do_not_resubmit") is False


def test_build_panel_poll_summary_dispatched_without_poll() -> None:
    summary = build_panel_poll_summary(
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        poll_results=None,
        polled=False,
    )
    assert summary["status"] == "dispatched"
    assert summary["member_status"] == {
        "skeptic": "running",
        "reviewer": "running",
    }
    assert summary["tokens_in"] == 0
    assert summary["tokens_out"] == 0


def test_build_panel_poll_summary_failed() -> None:
    summary = build_panel_poll_summary(
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"error": {"code": "validation_error"}},
        },
        poll_results={
            "skeptic": {"status": "failed", "error": {"code": "step_timeout"}},
        },
        polled=True,
    )
    assert summary["status"] == "failed"
    assert summary["member_status"]["skeptic"] == "failed"
    assert summary["member_status"]["reviewer"] == "failed"


def test_panel_result_envelope_poll_summary_do_not_resubmit() -> None:
    plan = admit_panel_plan(disposition="panel")
    assert not isinstance(plan, dict)
    member_models = {"skeptic": "xai/grok-4.6", "reviewer": "openai/gpt-5.6-terra"}
    poll_summary = build_panel_poll_summary(
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        poll_results={
            "skeptic": {"status": "running"},
            "reviewer": {"status": "completed"},
        },
        polled=True,
    )
    envelope = panel_result_envelope(
        plan=plan,
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        member_models=member_models,
        poll_summary=poll_summary,
    )
    assert envelope["do_not_resubmit"] is True
    assert envelope["in_flight_execution_ids"] == ["e1"]
    assert envelope["status"] == "partial"


def test_team_dispatch_yaml_concurrency_respond_floor() -> None:
    if not _TEAM_DISPATCH_YAML.is_file():
        pytest.skip(f"missing local pipeline fixture {_TEAM_DISPATCH_YAML}")
    data = yaml.safe_load(_TEAM_DISPATCH_YAML.read_text(encoding="utf-8"))
    concurrency_timeout = data["concurrency"]["timeout_seconds"]
    respond_timeout = next(
        step["timeout_seconds"] for step in data["steps"] if step["name"] == "respond"
    )
    assert concurrency_timeout >= respond_timeout


def test_panel_result_envelope_member_knob_resolution() -> None:
    plan = admit_panel_plan(disposition="panel")
    assert not isinstance(plan, dict)
    member_models = {
        "skeptic": "xai/grok-4.6",
        "reviewer": "openai/gpt-5.6-terra",
    }
    envelope = panel_result_envelope(
        plan=plan,
        dispatches={
            "skeptic": {"execution_id": "e1"},
            "reviewer": {"execution_id": "e2"},
        },
        member_models=member_models,
        reasoning_effort="high",
    )
    assert set(envelope["member_knob_resolution"]) == set(member_models)
    reviewer = envelope["member_knob_resolution"]["reviewer"]
    assert reviewer["status"] == "mapped"
    assert reviewer["reasoning_native"] == {"effort": "high"}
    assert "stamp_warnings" in envelope
    panel_caps = envelope["panel_capabilities"]
    assert panel_caps["skeptic"]["inline_only"] is False
    assert panel_caps["reviewer"]["mcp_connector_active"] is True
