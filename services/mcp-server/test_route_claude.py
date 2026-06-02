"""Tests for /mcp Claude dispatcher route (Phase D).

Four concerns:
  1. tools/list returns 14 domain dispatcher tools matching derive_claude_manifest()
  2. Primary tool count ≤ 24 (D3 P10 probe)
  3. Previously-absent domains (manage, pipeline, rag, observability) are
     accessible via dispatch (callpath wired, not just listed)
  4. /mcp/grok regression — wire_grok_route still builds; tools present
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

    mcp, pre_prune_tool_objects, overflow_metadata, overflow_registry = _build_server()
    manifest = derive_claude_manifest(CANONICAL_YAML)
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "manifest": manifest,
        "tools": tools,
        "tool_names": {t.name for t in tools},
        "primary_tools": _PRIMARY_TOOLS,
        "pre_prune_tool_objects": pre_prune_tool_objects,
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
    """D-T2: Claude /mcp exposes exactly 14 dispatcher domains; cap ≤ 24 (D3/P10).

    git_* (5) demoted off mcp_claude → overflow (thread 1179,
    decision:cursorbuild-ide-interface): 18 → 13; cortex_boot promoted → 14.
    """
    manifest = main_server_state["manifest"]
    assert len(manifest) == 14, (
        f"Expected 14 Claude dispatcher domains, got {len(manifest)}: "
        f"{sorted(e['domain'] for e in manifest)}"
    )
    assert len(manifest) <= 24, f"D3 cap violated: {len(manifest)} > 24"


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


# ── Test 4: grok regression ────────────────────────────────────────────────────


def test_claude_regression_grok_unaffected(main_server_state: dict) -> None:
    """D-T4: wire_grok_route still builds; /mcp/grok retains 80 tools."""
    from _derive import derive_grok_manifest  # noqa: PLC0415
    from grok_route import build_grok_server  # noqa: PLC0415

    pre_prune = main_server_state["pre_prune_tool_objects"]
    overflow_metadata = main_server_state["overflow_metadata"]
    overflow_registry = main_server_state["overflow_registry"]
    grok_mcp = build_grok_server(
        pre_prune,
        overflow_metadata=overflow_metadata,
        overflow_registry=overflow_registry,
    )
    grok_tools = asyncio.run(grok_mcp.list_tools())
    grok_manifest = derive_grok_manifest(CANONICAL_YAML)
    assert len(grok_tools) == len(grok_manifest), (
        f"Grok tool count mismatch: {len(grok_tools)} registered vs "
        f"{len(grok_manifest)} in canonical.yaml"
    )
    assert len(grok_manifest) == 80, (
        f"Grok manifest length changed: {len(grok_manifest)} (expected 80)"
    )
