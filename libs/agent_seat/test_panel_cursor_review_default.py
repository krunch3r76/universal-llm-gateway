"""Offline tests — panel reviewer default dispatches on cursor-sdk."""

from __future__ import annotations

import pytest

from agent_seat.panel_dispatch import (
    DEFAULT_PANEL_MEMBERS,
    PanelMemberSpec,
    build_team_dispatch_body,
    effective_model_for_member,
    panel_identity_labels,
    resolve_panel_members,
)

pytestmark = pytest.mark.offline


def test_default_panel_reviewer_is_cursor_fable() -> None:
    assert ("reviewer", "cursor/claude-fable-5-1") in DEFAULT_PANEL_MEMBERS


def test_build_body_uses_seat_for_cursor_reviewer() -> None:
    body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="reviewer", model="cursor/claude-fable-5-1"),
        dispatch_thread_id="t1",
    )
    assert body["seat"] == "cursor-sdk"
    assert body["model"] == "cursor/claude-fable-5-1"
    assert body["lane"] == "A"
    assert "role" not in body


def test_cursor_reviewer_omits_reasoning_effort() -> None:
    """BIND_B: panel fan-out must not forward effort onto cursor-sdk members."""
    body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="reviewer", model="cursor/claude-fable-5-1"),
        dispatch_thread_id="t1",
        reasoning_effort="high",
    )
    assert "reasoning_effort" not in body
    api_body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="skeptic", model="xai/grok-4.6"),
        dispatch_thread_id="t1",
        reasoning_effort="high",
    )
    assert api_body["reasoning_effort"] == "high"


def test_build_body_keeps_role_for_api_skeptic() -> None:
    body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="skeptic", model="xai/grok-4.6"),
        dispatch_thread_id="t1",
    )
    assert body["role"] == "skeptic"
    assert body["model"] == "xai/grok-4.6"
    assert "seat" not in body


def test_default_panel_has_two_distinct_identities() -> None:
    members = resolve_panel_members()
    member_models = {
        m.role: effective_model_for_member(m) for m in members
    }
    labels = panel_identity_labels(member_models)
    assert any(label.startswith("claude-fable-5-1@") for label in labels)
    assert any(label.startswith("grok-4.6@") for label in labels)
    assert len(labels) >= 2
