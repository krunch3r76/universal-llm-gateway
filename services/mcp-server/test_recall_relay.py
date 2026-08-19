"""Recall MCP relay — import graph, cx routing, and life-vs-code surface split.

Hermetic tests for the G2 recall sibling tool: thin cx relay to
POST /graph/recall/{matter|continuity}, 422 validation, and life-only registration.
"""

from __future__ import annotations

import ast
import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MCP_SERVER_DIR = REPO_ROOT / "services" / "mcp-server"
RECALL_MODULE = MCP_SERVER_DIR / "tools" / "recall.py"

sys.path.insert(0, str(MCP_SERVER_DIR))
sys.path.insert(0, str(REPO_ROOT / "libs"))
os.environ.setdefault("MCP_AUTH_TOKEN", "test-noop")
os.environ.setdefault("MCP_OAUTH_DISABLED", "1")

_WRITE_DISPATCH_MARKERS = (
    "create_entity_impl",
    "team_dispatch",
    "cursor_sdk_generate",
    "dispatch_cursor_sdk_generate",
    "implement_admission_bridge",
)


def _life_server() -> dict[str, Any]:
    from server import _build_server

    mcp, overflow_md, overflow_reg = _build_server("life")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tool_names": {t.name for t in tools},
        "overflow_md": overflow_md,
        "overflow_reg": overflow_reg,
    }


def _code_server() -> dict[str, Any]:
    from server import _build_server

    mcp, overflow_md, overflow_reg = _build_server("code")
    tools = asyncio.run(mcp.list_tools())
    return {
        "mcp": mcp,
        "tool_names": {t.name for t in tools},
        "overflow_md": overflow_md,
        "overflow_reg": overflow_reg,
    }


def _capture_recall_fn():
    from tools.recall import register_recall_tools

    class _CaptureMCP:
        def tool(self, *args: object, **kwargs: object):  # noqa: ANN201
            def decorator(fn: object) -> object:
                self.fn = fn
                return fn

            if args and callable(args[0]) and not kwargs:
                self.fn = args[0]
                return args[0]
            return decorator

    mcp = _CaptureMCP()
    register_recall_tools(mcp)
    return mcp.fn


def test_recall_import_graph_has_no_write_dispatch_impls() -> None:
    """recall.py must stay a thin relay with no write or dispatch imports."""
    source = RECALL_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for marker in _WRITE_DISPATCH_MARKERS:
                assert marker not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                for marker in _WRITE_DISPATCH_MARKERS:
                    assert marker not in alias.name


def test_recall_matter_relay_routes_to_graph_recall_matter() -> None:
    """matter op relays POST /graph/recall/matter with parsed arguments body."""
    payload = {"resolved": [], "nulls": [], "disclosure": {}}
    captured: list[tuple[str, str, dict[str, Any]]] = []

    def _fake_cx(method: str, path: str, body: dict[str, Any] | None = None, **_kw: object):
        captured.append((method, path, body or {}))
        return payload

    recall_fn = _capture_recall_fn()
    with patch("tools.recall.cx", side_effect=_fake_cx):
        result = recall_fn("matter", arguments='{"q": "chase escrow"}')
    assert result == payload
    assert captured == [("POST", "/graph/recall/matter", {"q": "chase escrow"})]


def test_recall_continuity_relay_routes_to_graph_recall_continuity() -> None:
    """continuity op relays POST /graph/recall/continuity with parsed arguments body."""
    payload = {"resolved": [], "nulls": [], "disclosure": {}}
    captured: list[tuple[str, str, dict[str, Any]]] = []

    def _fake_cx(method: str, path: str, body: dict[str, Any] | None = None, **_kw: object):
        captured.append((method, path, body or {}))
        return payload

    recall_fn = _capture_recall_fn()
    with patch("tools.recall.cx", side_effect=_fake_cx):
        result = recall_fn(
            "continuity",
            arguments='{"q": "where did we leave the tax appeal"}',
        )
    assert result == payload
    assert captured == [
        (
            "POST",
            "/graph/recall/continuity",
            {"q": "where did we leave the tax appeal"},
        )
    ]


def test_recall_unknown_op_returns_422() -> None:
    """Unknown recall op returns 422 with available-op listing."""
    recall_fn = _capture_recall_fn()
    result = recall_fn("bogus", arguments='{"q": "x"}')
    assert result.get("status_code") == 422
    assert "Unknown recall op" in result.get("error", "")


def test_recall_bad_arguments_returns_422() -> None:
    """Malformed arguments JSON returns the same 422 shape as imprint."""
    recall_fn = _capture_recall_fn()
    result = recall_fn("matter", arguments="not-json")
    assert result.get("status_code") == 422
    assert "arguments must be a JSON object" in result.get("error", "")


def test_recall_present_on_life_absent_on_code() -> None:
    """recall registers on life _build_server tool names and stays off code."""
    life = _life_server()
    code = _code_server()
    assert "recall" in life["tool_names"]
    assert "recall" not in code["tool_names"]
