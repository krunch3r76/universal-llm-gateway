"""MCP chat_session relay purity and catalog parity gates."""

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

_FORBIDDEN_CHAT_SESSION_MODULES = frozenset(
    {"claude_bundles", "web_chat_relay", "chat_harvest"}
)


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


def test_chat_session_on_both_surfaces(life_server: dict, code_server: dict) -> None:
    assert "chat_session" in life_server["tool_names"]
    assert "chat_session" in code_server["tool_names"]
    assert "chat_session" in life_server["primary"]
    assert "chat_session" in code_server["primary"]


def test_per_op_mandate_safety_in_catalog() -> None:
    import yaml

    raw = yaml.safe_load(CANONICAL_YAML.read_text(encoding="utf-8"))
    by_name = {row["canonical_name"]: row for row in raw["tools"]}
    assert by_name["chat_session_harvest"]["mandate_safety"] == "read_only"
    assert by_name["chat_session_probe"]["mandate_safety"] == "read_only"
    assert by_name["chat_session_paste"]["mandate_safety"] == "write"


def test_chat_session_module_has_no_forbidden_imports() -> None:
    source = (MCP_SERVER_DIR / "tools" / "chat_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)
    }
    import_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imports.intersection(_FORBIDDEN_CHAT_SESSION_MODULES)
    assert not any(
        m and any(m.startswith(prefix) for prefix in _FORBIDDEN_CHAT_SESSION_MODULES)
        for m in import_from
    )
