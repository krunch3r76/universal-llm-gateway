"""Tests for role entity sync attributes and dispatch capability resolution."""

from __future__ import annotations

import pytest

from agent_seat.profiles import derive_inline_only, get_profile, get_role
from agent_seat.role_entity_sync import (
    build_role_execution_attributes,
    resolve_dispatch_capabilities,
)


def test_skeptic_role_execution_attributes_mcp_capable() -> None:
    role = get_role("skeptic")
    profile = get_profile(role.default_family, role.default_platform)
    assert not derive_inline_only(profile)
    attrs = build_role_execution_attributes("skeptic", role, profile)
    assert attrs["capability_tier"] is None
    assert attrs["tool_surface"] == "mcp"
    assert attrs["mcp_required"] is True


def test_reviewer_role_execution_attributes_mcp_capable() -> None:
    role = get_role("reviewer")
    profile = get_profile(role.default_family, role.default_platform)
    attrs = build_role_execution_attributes("reviewer", role, profile)
    assert attrs["capability_tier"] is None
    assert attrs["tool_surface"] == "mcp"
    assert attrs["mcp_required"] is True


def test_resolve_dispatch_capabilities_skeptic_default() -> None:
    caps = resolve_dispatch_capabilities(model="xai/grok-4.5")
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["mcp_mechanism"] == "client_side_injection"
    assert caps["tool_surface"] == "mcp"


def test_resolve_dispatch_capabilities_reviewer_default() -> None:
    caps = resolve_dispatch_capabilities(model="openai/gpt-5.6-terra")
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["mcp_mechanism"] == "client_side_injection"
    assert caps["tool_surface"] == "mcp"


def test_resolve_dispatch_capabilities_anthropic_remote_connector() -> None:
    caps = resolve_dispatch_capabilities(model="anthropic/claude-opus-4-8")
    assert caps["mcp_mechanism"] == "remote_connector"


def test_resolve_dispatch_capabilities_effective_gate_false_overrides_model() -> None:
    """Caller ``mcp=False`` on a tool-capable model: echo is single-sourced with
    the effective gate, not the model-only base admission (thread 1653 drift)."""
    caps = resolve_dispatch_capabilities(model="openai/gpt-5.5", mcp_enabled=False)
    assert caps["inline_only"] is True
    assert caps["mcp_connector_active"] is False
    assert caps["tool_surface"] == "inline-only"


def test_resolve_dispatch_capabilities_cursor_sdk_preview_short_circuits_card() -> None:
    caps = resolve_dispatch_capabilities(model="cursor/composer-2.5")
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True


def test_resolve_dispatch_capabilities_effective_gate_true_passthrough() -> None:
    """Effective gate ``True`` flows straight through to the echoed surface."""
    caps = resolve_dispatch_capabilities(
        model="anthropic/claude-opus-4-8", mcp_enabled=True
    )
    assert caps["inline_only"] is False
    assert caps["mcp_connector_active"] is True
    assert caps["tool_surface"] == "mcp"


@pytest.mark.asyncio
async def test_roster_defaults_never_raise_capability_card_outside_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skeptic blast-radius: card reader must not escape outside dispatch admission."""
    from model_capabilities import CapabilityCardError
    from model_id import ModelId

    from agent_seat import hydration as _hyd
    from agent_seat.hydration import hydrate_agent
    from agent_seat.profiles import get_profile, get_role, load_roles

    class _Empty:
        async def __call__(self, path: str) -> dict[str, object]:
            return {}

    monkeypatch.setattr(_hyd, "_cortex_get", _Empty())
    monkeypatch.setattr(_hyd, "_bus_get", _Empty())

    for role_name in load_roles():
        role = get_role(role_name)
        profile = get_profile(role.default_family, role.default_platform)
        build_role_execution_attributes(role_name, role, profile)
        default_model = role.default_model or profile.default_model
        if not default_model:
            continue
        if ModelId.parse(default_model).backend_type == "cursor_sdk":
            resolve_dispatch_capabilities(model=default_model, mcp_enabled=None)
            continue
        resolve_dispatch_capabilities(model=default_model, mcp_enabled=None)
        try:
            bundle = await hydrate_agent(role_name, model=default_model)
        except CapabilityCardError as exc:
            raise AssertionError(
                f"hydrate_agent raised CapabilityCardError for {role_name} "
                f"+ {default_model}: {exc}"
            ) from exc
        assert isinstance(bundle.inline_only, bool)
