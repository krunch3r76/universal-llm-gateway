"""Offline tests — panel reviewer default dispatches on cursor-sdk."""

from __future__ import annotations

import pytest

from agent_seat.panel_dispatch import (
    DEFAULT_PANEL_MEMBERS,
    PanelMemberSpec,
    build_team_dispatch_body,
    provider_family_label,
    resolve_panel_members,
)

pytestmark = pytest.mark.offline


def test_default_panel_reviewer_is_cursor_terra() -> None:
    assert ("reviewer", "cursor/gpt-5.6-terra") in DEFAULT_PANEL_MEMBERS


def test_build_body_uses_seat_for_cursor_reviewer() -> None:
    body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="reviewer", model="cursor/gpt-5.6-terra"),
        dispatch_thread_id="t1",
    )
    assert body["seat"] == "cursor-sdk"
    assert body["model"] == "cursor/gpt-5.6-terra"
    assert "role" not in body


def test_cursor_reviewer_omits_reasoning_effort() -> None:
    """BIND_B: panel fan-out must not forward effort onto cursor-sdk members."""
    body = build_team_dispatch_body(
        spec=PanelMemberSpec(role="reviewer", model="cursor/gpt-5.6-terra"),
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


def test_provider_family_label_for_cursor_gpt() -> None:
    assert provider_family_label("cursor/gpt-5.6-terra") == "GPT"
    assert provider_family_label("xai/grok-4.6") == "Grok"


def test_default_panel_has_two_families() -> None:
    members = resolve_panel_members()
    labels = {provider_family_label(m.model or "") for m in members if m.model}
    # skeptic uses role default (xai) when model is None — resolve via effective
    from agent_seat.panel_dispatch import effective_model_for_member

    labels = {provider_family_label(effective_model_for_member(m)) for m in members}
    assert "GPT" in labels
    assert "Grok" in labels
    assert len(labels) >= 2
