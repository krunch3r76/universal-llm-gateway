"""Tests for profile-derived seat capability tokens (B2 Slice A)."""

from __future__ import annotations

from agent_seat.profiles import (
    CAPABILITY_TOKENS,
    get_profile,
    seat_capabilities,
    seat_capability_map,
)


def test_capability_tokens_closed_enum() -> None:
    assert CAPABILITY_TOKENS == frozenset({"mcp_fs", "local_fs_write", "git_worktree"})


def test_mcp_seat_has_mcp_fs_not_git_worktree() -> None:
    prof = get_profile("claude", "web")
    assert prof.tool_surface == "mcp"
    toks = seat_capabilities(prof)
    assert "mcp_fs" in toks
    assert "local_fs_write" in toks
    assert "git_worktree" not in toks


def test_inline_only_seat_has_no_mcp_fs() -> None:
    prof = get_profile("grok", "api-multi")
    assert prof.tool_surface == "inline-only"
    toks = seat_capabilities(prof)
    assert "mcp_fs" not in toks
    assert "local_fs_write" not in toks


def test_sdk_seat_has_git_worktree() -> None:
    prof = get_profile("cursor", "sdk")
    toks = seat_capabilities(prof)
    assert "git_worktree" in toks
    assert "mcp_fs" not in toks


def test_seat_capability_map_derived_from_profiles() -> None:
    mapping = seat_capability_map()
    assert "claude-web" in mapping
    assert "mcp_fs" in mapping["claude-web"]
    assert mapping["claude-web"].issubset(CAPABILITY_TOKENS)
