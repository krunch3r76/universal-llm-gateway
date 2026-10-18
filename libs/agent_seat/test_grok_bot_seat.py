"""Tests for `grok/bot` capability profile and session-ID grammar (a:39611)."""

from __future__ import annotations

from agent_seat.profiles import get_profile
from agent_seat.registry import (
    _bus_address_map,
    normalize_agent_slug,
    normalize_bus_address,
    resolve_capability_cell_from_bus_address,
)
from agent_seat.session_id import SESSION_ID_EXAMPLES, SESSION_ID_RE


def test_grok_bot_capability_profile_loads_on_code_surface() -> None:
    profile = get_profile("grok", "bot")
    assert profile.family == "grok"
    assert profile.platform == "bot"
    assert profile.provider == "xai"
    assert profile.tool_surface == "mcp"
    assert profile.mcp_surface == "code"
    assert profile.delivery == "manual"
    assert profile.include_deadlines is False
    assert profile.include_review_queue is False
    assert profile.confirm_and_proceed is False
    assert profile.addenda == ()
    assert profile.admits_generate() is False


def test_grok_bot_excluded_from_bus_address_fold() -> None:
    assert "bot-xai" not in _bus_address_map().values()
    assert normalize_bus_address("grok-bot") == "grok-bot"
    assert normalize_agent_slug("grok-bot") == "grok-bot"
    assert resolve_capability_cell_from_bus_address("grok-bot") == ("grok", "bot")


def test_grok_bot_session_id_matches_grammar() -> None:
    example = "grok-bot-2026-10-18-032900-b48"
    assert example in SESSION_ID_EXAMPLES
    assert SESSION_ID_RE.match(example) is not None
