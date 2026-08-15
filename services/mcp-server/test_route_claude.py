"""Tests for /mcp/code dispatcher route (Phase D + dual-endpoint split).

Three concerns:
  1. tools/list on code surface matches ``surface_primary_domains`` binding
  2. Primary tool count ≤ 24 (D3 P10 probe)
  3. Code-only domains are primary on /mcp/code
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

_CODE_ONLY_PRIMARY = {
    "manage",
    "observability",
    "pipeline",
    "team_dispatch",
    "panel_dispatch",
}


@pytest.fixture(scope="module")
def main_server_state() -> dict:
    """Build code-surface server once for all tests in this module."""
    from endpoint_surface import derive_surface_primary_tools
    from server import _PRIMARY_TOOLS, _build_server

    mcp, overflow_metadata, overflow_registry = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tools": tools,
        "tool_names": {t.name for t in tools},
        "primary_tools": _PRIMARY_TOOLS,
        "surface_primary": derive_surface_primary_tools("code"),
        "overflow_metadata": overflow_metadata,
        "overflow_registry": overflow_registry,
    }


def test_code_primary_tools_match_surface_binding(main_server_state: dict) -> None:
    """Code /mcp tools/list matches ``surface_primary_domains.code``."""
    assert main_server_state["tool_names"] == set(main_server_state["surface_primary"])
    assert main_server_state["primary_tools"] == main_server_state["surface_primary"]


def test_code_primary_tools_count(main_server_state: dict) -> None:
    """D-T2: code surface exposes 19 primary tools; cap ≤ 24 (D3/P10)."""
    primary = main_server_state["primary_tools"]
    assert len(primary) == 19
    assert len(primary) <= 24
    assert "skill_suggest" not in primary


def test_skill_suggest_hidden_but_dispatchable(main_server_state: dict) -> None:
    assert "skill_suggest" not in main_server_state["tool_names"]
    assert "skill_suggest" not in main_server_state["overflow_metadata"]
    assert "skill_suggest" in main_server_state["overflow_registry"]


def test_code_dispatch_family_primary(main_server_state: dict) -> None:
    assert _CODE_ONLY_PRIMARY <= main_server_state["primary_tools"]
