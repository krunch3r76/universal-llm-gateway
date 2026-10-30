"""Tests for read-only cortex_read companion tool."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cortex_store.dispatch_ops import _OPS as DISPATCH_OPS  # noqa: E402
from tools._cortex_read import (  # noqa: E402
    CORTEX_READ_OPS_CODE,
    CORTEX_READ_OPS_LIFE,
    cortex_read_admitted_ops,
    register_cortex_read_tool,
)

GOLDEN_LIFE = CORTEX_READ_OPS_LIFE
GOLDEN_CODE = CORTEX_READ_OPS_CODE


@pytest.fixture
def cortex_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools._cortex_read.record", lambda *a, **k: None)
    monkeypatch.setattr(
        "tools._cortex_read.toolprogress_begin", lambda *a, **k: (None, None)
    )
    monkeypatch.setattr("tools._cortex_read.toolprogress_end", lambda *a, **k: None)
    monkeypatch.setattr(
        "tools._cortex_read.current_request_metadata",
        lambda: {"surface": "code"},
    )
    mcp = FastMCP("test-cortex-read")
    register_cortex_read_tool(mcp, surface="code")
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "cortex_read")
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    return tool.fn


def test_cortex_read_golden_set_life() -> None:
    assert cortex_read_admitted_ops("life") == GOLDEN_LIFE
    assert "activate" in GOLDEN_LIFE
    assert "cortex_brief" not in GOLDEN_LIFE


def test_cortex_read_golden_set_code() -> None:
    assert cortex_read_admitted_ops("code") == GOLDEN_CODE


@pytest.mark.parametrize(
    "op",
    sorted(set(DISPATCH_OPS) - GOLDEN_CODE),
)
def test_cortex_read_rejects_non_admitted_dispatch_ops(cortex_read_fn, op: str) -> None:
    out = cortex_read_fn(tool=op, arguments="{}")
    assert out.get("reason") == "not_a_read_op"


def test_cortex_read_rejects_write_ops(cortex_read_fn) -> None:
    out = cortex_read_fn(tool="entity_create", arguments='{"entity_id": "x:y"}')
    assert out.get("reason") == "not_a_read_op"


def test_cortex_read_rejects_cortex_brief(cortex_read_fn) -> None:
    out = cortex_read_fn(tool="cortex_brief", arguments='{"seat": "orion"}')
    assert out.get("reason") == "not_a_read_op"


def test_cortex_read_entity_get_handoff(cortex_read_fn) -> None:
    payload = {"entity_id": "todo:orion-readonly-mcp-surfaces", "name": "Orion"}
    with patch("tools._cortex_read.cx", return_value=payload) as cx_mock:
        out = cortex_read_fn(
            tool="entity_get",
            arguments=json.dumps({"entity_id": "todo:orion-readonly-mcp-surfaces"}),
        )
    assert out == payload
    body = cx_mock.call_args[0][2]
    assert body["tool"] == "entity_get"


def test_cortex_read_search_orion_todo(cortex_read_fn) -> None:
    payload = {"items": [{"entity_id": "todo:orion-readonly-mcp-surfaces"}]}
    with patch("tools._cortex_read.cx", return_value=payload):
        out = cortex_read_fn(
            tool="search",
            arguments=json.dumps({"query": "orion-readonly-mcp-surfaces"}),
        )
    assert out.get("items")


def test_endpoint_op_allowed_uses_cortex_not_cortex_read(cortex_read_fn) -> None:
    """Family-None guard is the allow-list; endpoint_op_allowed checks cortex only."""
    with patch(
        "tools._cortex_read.endpoint_op_allowed", return_value=(True, None)
    ) as eoa_mock:
        with patch("tools._cortex_read.cx", return_value={"ok": True}):
            cortex_read_fn(tool="entity_get", arguments='{"entity_id": "a:b"}')
    eoa_mock.assert_called_once()
    assert eoa_mock.call_args[0][1] == "cortex"
