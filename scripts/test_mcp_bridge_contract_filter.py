"""Unit tests for cursor-sdk stdio MCP contract filter (G5)."""

from __future__ import annotations

import ast
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_bridge_contract_filter import (  # noqa: E402
    FILTERED_CONTRACTS,
    ULG_MCP_CONTRACT_ENV,
    filter_tools_list_payload,
    read_framed_message,
    should_filter_stdio,
    write_framed_message,
)

TOOLS_LIST_FIXTURE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "tools": [
            {"name": "cortex", "description": "cortex"},
            {"name": "team_dispatch", "description": "dispatch"},
            {"name": "project_ask", "description": "ask"},
            {"name": "fs", "description": "fs"},
        ]
    },
}


def test_should_filter_stdio_unset_is_false() -> None:
    assert not should_filter_stdio({})


@pytest.mark.parametrize("contract", sorted(FILTERED_CONTRACTS))
def test_should_filter_stdio_for_implement_contracts(contract: str) -> None:
    assert should_filter_stdio({ULG_MCP_CONTRACT_ENV: contract})


def test_filter_tools_list_payload_trims_hidden_names() -> None:
    allow = frozenset({"cortex", "fs"})
    filtered = filter_tools_list_payload(TOOLS_LIST_FIXTURE, allow)
    names = {t["name"] for t in filtered["result"]["tools"]}
    assert names == allow
    assert "team_dispatch" not in names
    assert "project_ask" not in names


def test_filter_tools_list_payload_passthrough_non_tools_response() -> None:
    payload = {"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}
    assert filter_tools_list_payload(payload, frozenset({"cortex"})) == payload


def test_framing_roundtrip() -> None:
    payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 3}
    buf = io.BytesIO()
    write_framed_message(buf, payload)
    buf.seek(0)
    assert read_framed_message(buf) == payload


def test_bridge_main_uses_execve_when_unfiltered() -> None:
    bridge_path = REPO_ROOT / "scripts" / "mcp-fastmcp-remote-bridge.py"
    tree = ast.parse(bridge_path.read_text(encoding="utf-8"))
    execve_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execve"
    ]
    assert execve_calls, "expected os.execve on unfiltered path"
    main_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    main_src = ast.get_source_segment(bridge_path.read_text(encoding="utf-8"), main_fn) or ""
    assert "should_filter_stdio" in main_src
    assert "run_filtered_stdio_proxy" in main_src


def test_filter_fixture_json_serializable() -> None:
    json.dumps(TOOLS_LIST_FIXTURE)
