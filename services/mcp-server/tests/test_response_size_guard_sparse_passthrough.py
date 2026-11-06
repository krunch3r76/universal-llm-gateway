"""Sparse document payload passthrough for response size guard."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams
from response_size_guard import ResponseSizeGuard, _node_is_sparse_document_payload
from tools._sparse_document_read import SPARSE_POLICY_SLICE1
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline


def test_sparse_policy_root_only_passthrough() -> None:
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "manifest"}
    )
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "export"}
    )
    assert not _node_is_sparse_document_payload({"selection": "manifest"})
    assert not _node_is_sparse_document_payload(
        {"sparse_policy": "other", "selection": "manifest"}
    )


def test_multi_selection_passes_on_root_marker_without_batch_flag() -> None:
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "multi"}
    )
    assert not _node_is_sparse_document_payload({"sparse_section_batch": True})
    assert not _node_is_sparse_document_payload(
        {"sparse_explicit_export": True, "selection": "export"}
    )


def test_nested_sparse_dict_does_not_passthrough() -> None:
    from response_size_guard import _sparse_document_read_passthrough

    class _Result:
        structured_content = {
            "wrapper": {
                "sparse_policy": SPARSE_POLICY_SLICE1,
                "selection": "manifest",
            }
        }

    assert _sparse_document_read_passthrough(_Result()) is False  # type: ignore[arg-type]


def test_section_chunk_passes_guard_with_root_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """md_read/read_section 64KiB chunk stays unclipped when selection=section."""
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    monkeypatch.setattr("response_size_guard._CURSOR_THRESHOLD", 1024)
    monkeypatch.setattr("response_size_guard._OVERRIDE_THRESHOLD", None)
    body = "x" * 70000
    (tmp_path / "wide.md").write_text(f"# Wide\n\n{body}\n", encoding="utf-8")
    mcp = FastMCP("guard-chunk")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    fn = next(tool for tool in tools if tool.name == "markdown").fn
    payload = fn(
        op="read_section",
        path="wide.md",
        sandbox="cortex",
        section="Wide",
        chunk_cursor=0,
    )
    assert payload["selection"] == "section"
    assert payload["truncated"] is True
    assert len(payload["content"]) == 65536
    result = ToolResult(structured_content=payload)
    ctx = MagicMock()
    ctx.message = CallToolRequestParams(name="markdown", arguments={"op": "read_section"})
    out = asyncio.run(
        ResponseSizeGuard().on_call_tool(ctx, AsyncMock(return_value=result))
    )
    assert out.structured_content["selection"] == "section"
    assert out.structured_content["content"] == payload["content"]
    text = ""
    if out.content and not isinstance(out.content, str):
        text = getattr(out.content[0], "text", "") or ""
    elif isinstance(out.content, str):
        text = out.content
    assert "exceeded size limit" not in text
