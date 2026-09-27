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
    _copy_downstream,
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


def test_bridge_main_always_proxies_no_execve() -> None:
    bridge_path = REPO_ROOT / "scripts" / "mcp-fastmcp-remote-bridge.py"
    source = bridge_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    execve_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execve"
    ]
    assert not execve_calls, "execve path removed — all contracts must proxy"
    main_fn = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    main_src = ast.get_source_segment(source, main_fn) or ""
    assert "should_filter_stdio" in main_src
    assert "run_filtered_stdio_proxy" in main_src


def test_filter_fixture_json_serializable() -> None:
    json.dumps(TOOLS_LIST_FIXTURE)


def _tools_call(arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "team_dispatch", "arguments": arguments},
    }


def _drive_downstream(
    message: dict, *, review_gate: bool
) -> tuple[dict | None, dict | None]:
    client_in = io.BytesIO()
    write_framed_message(client_in, message)
    client_in.seek(0)
    child_in = io.BytesIO()
    client_out = io.BytesIO()
    _copy_downstream(
        child_in,
        client_in,
        pending_methods={},
        review_gate=review_gate,
        allow=frozenset({"team_dispatch", "cortex"}) if review_gate else None,
        client_out=client_out,
    )
    child_in.seek(0)
    client_out.seek(0)
    forwarded = read_framed_message(child_in)
    refused = read_framed_message(client_out)
    return forwarded, refused


_REVIEW_ARGS = {
    "op": "generate",
    "model": "cdp/opus-5",
    "purpose": "review",
    "contract": "none",
    "prompt": "diff",
    "dispatch_thread_id": "1",
}


def test_nested_filter_forwards_review_and_refuses_implement_spawn() -> None:
    forwarded, refused = _drive_downstream(_tools_call(_REVIEW_ARGS), review_gate=True)
    assert forwarded == _tools_call(_REVIEW_ARGS)
    assert refused is None
    spawn = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "implement",
        "model": "cursor/composer-2.5",
    }
    forwarded, refused = _drive_downstream(_tools_call(spawn), review_gate=True)
    assert forwarded is None
    assert refused is not None
    assert refused["error"]["code"] == -32602


def test_nested_filter_refuses_panel_dispatch_by_name() -> None:
    message = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {"name": "panel_dispatch", "arguments": {"op": "generate"}},
    }
    forwarded, refused = _drive_downstream(message, review_gate=True)
    assert forwarded is None
    assert refused is not None
    assert "panel_dispatch" in refused["error"]["message"]


def test_top_level_unfiltered_forwards_arbitrary_team_dispatch() -> None:
    """conductor handoff does not set ULG_MCP_CONTRACT, so the review gate is off."""
    assert not should_filter_stdio({ULG_MCP_CONTRACT_ENV: "conductor"})
    assert "conductor" not in FILTERED_CONTRACTS
    spawn = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "implement",
        "model": "cursor/composer-2.5",
    }
    forwarded, refused = _drive_downstream(_tools_call(spawn), review_gate=False)
    assert forwarded == _tools_call(spawn)
    assert refused is None
