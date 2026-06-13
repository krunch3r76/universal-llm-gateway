"""Tests for role entity sync attributes and dispatch capability resolution."""

from __future__ import annotations

from agent_seat.profiles import derive_inline_only, get_profile, get_role
from agent_seat.role_entity_sync import (
    build_role_execution_attributes,
    resolve_dispatch_capabilities,
)


def test_skeptic_role_execution_attributes_inline_only() -> None:
    role = get_role("skeptic")
    profile = get_profile(role.default_family, role.default_platform)
    assert derive_inline_only(profile)
    attrs = build_role_execution_attributes("skeptic", role, profile)
    assert attrs["capability_tier"] == "inline-only"
    assert attrs["tool_surface"] == "inline-only"
    assert attrs["mcp_required"] is False
    assert attrs["required_tools"] == []


def test_reviewer_role_execution_attributes_mcp_capable() -> None:
    role = get_role("reviewer")
    profile = get_profile(role.default_family, role.default_platform)
    attrs = build_role_execution_attributes("reviewer", role, profile)
    assert attrs["capability_tier"] is None
    assert attrs["tool_surface"] == "mcp"
    assert attrs["mcp_required"] is True


def test_resolve_dispatch_capabilities_skeptic_default() -> None:
    caps = resolve_dispatch_capabilities(model="xai/grok-4.20-multi-agent-0309")
    assert caps["inline_only"] is True
    assert caps["mcp_connector_active"] is False
    assert caps["tool_surface"] == "inline-only"


def test_resolve_dispatch_capabilities_reviewer_default() -> None:
    caps = resolve_dispatch_capabilities(model="openai/gpt-5.5")
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["tool_surface"] == "mcp"


def test_resolve_dispatch_capabilities_effective_gate_false_overrides_model() -> None:
    """Caller ``mcp=False`` on a tool-capable model: echo is single-sourced with
    the effective gate, not the model-only base admission (thread 1653 drift)."""
    caps = resolve_dispatch_capabilities(model="openai/gpt-5.5", mcp_enabled=False)
    assert caps["inline_only"] is True
    assert caps["mcp_connector_active"] is False
    assert caps["tool_surface"] == "inline-only"


def test_resolve_dispatch_capabilities_effective_gate_true_passthrough() -> None:
    """Effective gate ``True`` flows straight through to the echoed surface."""
    caps = resolve_dispatch_capabilities(
        model="anthropic/claude-opus-4-6", mcp_enabled=True
    )
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["tool_surface"] == "mcp"
