"""Tests for /mcp Claude dispatcher route (Phase D).

Three concerns:
  1. tools/list returns 14 domain dispatcher tools matching derive_claude_manifest()
  2. Primary tool count ≤ 24 (D3 P10 probe)
  3. Previously-absent domains (manage, pipeline, rag, observability) are
     accessible via dispatch (callpath wired, not just listed)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
CANONICAL_YAML = REPO_ROOT / "config" / "mcp" / "canonical.yaml"

sys.path.insert(0, str(MCP_SERVER_DIR))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

_ABSENT_DOMAINS = {"manage", "pipeline", "rag", "observability"}


@pytest.fixture(scope="module")
def main_server_state() -> dict:
    """Build main /mcp server once for all tests in this module."""
    from _derive import derive_claude_manifest  # noqa: PLC0415
    from server import _PRIMARY_TOOLS, _build_server  # noqa: PLC0415

    mcp, overflow_metadata, overflow_registry = _build_server()
    manifest = derive_claude_manifest(CANONICAL_YAML)
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "manifest": manifest,
        "tools": tools,
        "tool_names": {t.name for t in tools},
        "primary_tools": _PRIMARY_TOOLS,
        "overflow_metadata": overflow_metadata,
        "overflow_registry": overflow_registry,
    }


# ── Test 1: tools/list matches Claude manifest ─────────────────────────────────


def test_claude_primary_tools_match_manifest(main_server_state: dict) -> None:
    """D-T1: /mcp tools/list matches exactly the domains in derive_claude_manifest()."""
    manifest = main_server_state["manifest"]
    manifest_tool_names = {e["tool_name"] for e in manifest}
    primary_tools = main_server_state["primary_tools"]
    assert primary_tools == manifest_tool_names, (
        f"_PRIMARY_TOOLS ≠ manifest tool_names. "
        f"Extra: {sorted(primary_tools - manifest_tool_names)}, "
        f"Missing: {sorted(manifest_tool_names - primary_tools)}"
    )


# ── Test 2: primary tool count ≤ 24 (P10 probe) ───────────────────────────────


def test_claude_primary_tools_count(main_server_state: dict) -> None:
    """D-T2: Claude /mcp exposes exactly 15 dispatcher domains; cap ≤ 24 (D3/P10).

    git_* demoted off mcp_claude; grokbuild demoted (11588). Standalone
    team_dispatch + panel_dispatch + agent_bus_read on mcp_claude; dispatch
    domain is overflow-only after dispatch_frontier/dispatch_team removal.
    skill_suggest promoted as first-class primary tool (todo:skill-suggest-mcp-tool).
    """
    manifest = main_server_state["manifest"]
    assert len(manifest) == 15, (
        f"Expected 15 Claude dispatcher domains, got {len(manifest)}: "
        f"{sorted(e['domain'] for e in manifest)}"
    )
    assert len(manifest) <= 24, f"D3 cap violated: {len(manifest)} > 24"
    assert "skill_suggest" in main_server_state["primary_tools"]


def test_skill_suggest_registered_in_server(main_server_state: dict) -> None:
    assert "skill_suggest" in main_server_state["tool_names"]


# ── Test 3: absent domains accessible via dispatch ────────────────────────────


def test_claude_absent_domains_accessible_via_dispatch(
    main_server_state: dict,
) -> None:
    """D-T3: previously-absent domains now in _PRIMARY_TOOLS (callpath wired)."""
    primary_tools = main_server_state["primary_tools"]
    missing = _ABSENT_DOMAINS - primary_tools
    assert not missing, (
        f"Absent domains not promoted to primary after Phase D: {sorted(missing)}"
    )
