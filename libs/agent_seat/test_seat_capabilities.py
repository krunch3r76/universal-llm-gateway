"""Tests for profile-derived seat capability tokens (life/code MCP surfaces)."""

from __future__ import annotations

from agent_seat.profiles import (
    CAPABILITY_TOKENS,
    get_profile,
    seat_capabilities,
    seat_capability_map,
)


def test_capability_tokens_closed_enum() -> None:
    assert CAPABILITY_TOKENS == frozenset(
        {"mcp_life", "mcp_code", "local_fs_write", "git_worktree"}
    )


def test_life_seat_has_mcp_life_not_code() -> None:
    prof = get_profile("claude", "web")
    assert prof.tool_surface == "mcp"
    assert prof.mcp_surface == "life"
    toks = seat_capabilities(prof)
    assert "mcp_life" in toks
    assert "mcp_code" not in toks
    assert "local_fs_write" in toks
    assert "git_worktree" not in toks


def test_code_seat_satisfies_life_and_code() -> None:
    prof = get_profile("claude", "cursor")
    assert prof.mcp_surface == "code"
    toks = seat_capabilities(prof)
    assert "mcp_life" in toks
    assert "mcp_code" in toks


def test_sdk_seat_has_git_worktree_and_code() -> None:
    prof = get_profile("cursor", "sdk")
    toks = seat_capabilities(prof)
    assert "git_worktree" in toks
    assert "mcp_code" in toks
    assert "mcp_life" in toks


def test_seat_capability_map_derived_from_profiles() -> None:
    mapping = seat_capability_map()
    assert "claude-web" in mapping
    assert "mcp_life" in mapping["claude-web"]
    assert "mcp_code" not in mapping["claude-web"]
    assert mapping["claude-web"].issubset(CAPABILITY_TOKENS)
