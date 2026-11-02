"""Tests for capability-cell boot identity resolution."""

from __future__ import annotations

from agent_seat.boot_identity import (
    BootIdentity,
    legacy_boot_identity,
    resolve_boot_identity_for_seat,
)
from agent_seat.registry import normalize_bus_address


def test_legacy_boot_identity_matches_capability_cell() -> None:
    ident = legacy_boot_identity("claude", "web")
    assert ident.agent == "claude-web"
    assert ident.session_prefix == normalize_bus_address("claude-web")
    assert ident.inbox_recipient == "claude-web"
    assert ident.self_entity_id == "family:claude"


def test_resolve_legacy_cursor_unchanged() -> None:
    ident = resolve_boot_identity_for_seat("cursor")
    assert isinstance(ident, BootIdentity)
    assert ident.family == "claude"
    assert ident.platform == "cursor"


def test_resolve_grok_bot_legacy() -> None:
    ident = resolve_boot_identity_for_seat("grok-bot")
    assert isinstance(ident, BootIdentity)
    assert ident.family == "grok"
    assert ident.platform == "bot"
    assert ident.agent == "grok-bot"


def test_resolve_orion_seat_unresolved() -> None:
    out = resolve_boot_identity_for_seat("orion")
    assert isinstance(out, dict)
    assert out.get("reason") == "seat_unresolved"


def test_gpt_web_capability_non_lead_no_dispatch_grant() -> None:
    from agent_seat.profiles import (
        get_profile,
        load_lead_agent_slugs,
        seat_capabilities,
    )
    from agent_seat.registry import is_lead_agent

    profile = get_profile("gpt", "web")
    assert profile.api_dispatchable is False
    assert profile.auto_dispatchable is False
    assert profile.manual_handoff is False
    assert profile.admits_generate() is False
    assert profile.admits_handoff() is False
    assert "gpt-web" not in load_lead_agent_slugs()
    assert is_lead_agent("gpt-web") is False
    assert seat_capabilities(profile) == frozenset(
        {"mcp_life", "mcp_code", "local_fs_write"}
    )
