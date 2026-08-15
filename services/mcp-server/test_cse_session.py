"""MCP cse_session relay purity and catalog parity gates."""

from __future__ import annotations

import ast
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


@pytest.fixture(scope="module")
def life_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    return {
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("life"),
    }


@pytest.fixture(scope="module")
def code_server() -> dict:
    from endpoint_surface import derive_surface_primary_tools
    from server import _build_server

    mcp, _, _ = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    return {
        "tool_names": {t.name for t in tools},
        "primary": derive_surface_primary_tools("code"),
    }


def test_cse_session_on_both_surfaces(life_server: dict, code_server: dict) -> None:
    assert "cse_session" in life_server["tool_names"]
    assert "cse_session" in code_server["tool_names"]
    assert "cse_session" in life_server["primary"]
    assert "cse_session" in code_server["primary"]


def test_project_ask_still_code_only(life_server: dict, code_server: dict) -> None:
    from claude_bundles.operator_proxy_mission import LIFE_SURFACE_FORBIDDEN_TOOLS
    from endpoint_surface import derive_code_extra_primary_tools

    assert "project_ask" not in life_server["primary"]
    assert "project_ask" in code_server["primary"]
    assert LIFE_SURFACE_FORBIDDEN_TOOLS == derive_code_extra_primary_tools()
    assert "project_ask" in LIFE_SURFACE_FORBIDDEN_TOOLS
    assert "cse_session" not in LIFE_SURFACE_FORBIDDEN_TOOLS


def test_per_op_mandate_safety_in_catalog() -> None:
    import yaml

    raw = yaml.safe_load(CANONICAL_YAML.read_text(encoding="utf-8"))
    by_name = {row["canonical_name"]: row for row in raw["tools"]}
    assert by_name["cse_session_provenance"]["mandate_safety"] == "read_only"
    assert by_name["cse_session_harvest"]["mandate_safety"] == "read_only"
    assert by_name["cse_session_paste"]["mandate_safety"] == "write"


def test_relay_module_has_no_bundle_imports() -> None:
    source = (MCP_SERVER_DIR / "tools" / "cse_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    import_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "claude_bundles" not in imports
    assert "cdp_ask" not in imports
    assert not any(m and m.startswith("claude_bundles") for m in import_from)
    assert not any(m and m.startswith("cdp_ask") for m in import_from)
