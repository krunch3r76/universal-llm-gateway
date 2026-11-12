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
from tools._sparse_payload_envelope import SPARSE_POLICY_SLICE1
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
    ctx.message = CallToolRequestParams(
        name="markdown", arguments={"op": "read_section"}
    )
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


def _fs_read_multi(
    monkeypatch: pytest.MonkeyPatch, projects: Path, paths: list[str], *, export: bool
):
    from fs_impl import fs_impl

    monkeypatch.setattr(
        "fs_roots.fs_root_for",
        lambda surface, root_id: projects,
    )
    return fs_impl(
        surface="code",
        overflow_registry={},
        op="read_multi",
        sandbox="workspaces",
        path="",
        content="",
        target="",
        target_sandbox="",
        line=0,
        section="",
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=0,
        expected_sha256="",
        if_absent=False,
        paths=paths,
        export=export,
    )


def _guard_fs_payload(payload: dict):
    """Run one real fs payload through ResponseSizeGuard.on_call_tool."""
    import json

    from mcp.types import TextContent
    from response_size_guard import ResponseSizeGuard, _store

    async def call_next(_ctx):
        return ToolResult(
            content=[TextContent(type="text", text=json.dumps(payload))],
            structured_content=payload,
        )

    ctx = MagicMock()
    ctx.message = CallToolRequestParams(name="fs", arguments={"op": "read_multi"})
    before = set(_store)
    out = asyncio.run(ResponseSizeGuard().on_call_tool(ctx, call_next))
    new_ids = [key for key in _store if key not in before]
    return out, new_ids


def _fetch_stored(ref_id: str):
    """Redeem a guard reference through the real retrieve tool."""
    mcp = FastMCP("n6-fetch")
    from response_size_guard import register_response_guard

    register_response_guard(mcp)
    tools = asyncio.run(mcp.list_tools())
    retrieve = next(tool.fn for tool in tools if tool.name == "retrieve")
    return retrieve(id=ref_id)


def _wire_text(result: ToolResult) -> str:
    if isinstance(result.content, str):
        return result.content
    if result.content:
        return getattr(result.content[0], "text", "") or ""
    return ""


def test_large_nondocument_read_multi_is_externalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ~1 MiB binary beside a document must be stored, then fetch losslessly.

    sparse_policy stays on the stored payload. The wire result is the guard
    reference, not the original base64 body. Export and no-selector both.
    """
    import base64
    import re

    from response_size_guard import _store, _threshold_for_profile
    from tools._sparse_payload_envelope import SPARSE_POLICY_SLICE1

    projects = tmp_path / "projects"
    folder = projects / "universal-llm-gateway/notes"
    folder.mkdir(parents=True)
    raw = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4096
    assert len(raw) > _threshold_for_profile()
    (folder / "big.png").write_bytes(raw)
    (folder / "k.md").write_text("kept-document\n", encoding="utf-8")
    png_rel = "universal-llm-gateway/notes/big.png"
    md_rel = "universal-llm-gateway/notes/k.md"
    before = set(_store)
    try:
        for export in (True, False):
            payload = _fs_read_multi(
                monkeypatch, projects, [png_rel, md_rel], export=export
            )
            assert payload.get("sparse_policy") == SPARSE_POLICY_SLICE1
            assert payload.get("selection") == "multi"
            out, new_ids = _guard_fs_payload(payload)
            assert len(new_ids) == 1, (export, _wire_text(out)[:240], new_ids)
            text = _wire_text(out)
            assert "content_base64" not in text
            assert len(text.encode("utf-8")) < 1000
            match = re.search(r'retrieve\(id="([^"]+)"\)', text)
            assert match is not None, text
            assert match.group(1) == new_ids[0]
            fetched = _fetch_stored(new_ids[0])
            structured = fetched.structured_content
            assert structured["sparse_policy"] == SPARSE_POLICY_SLICE1
            assert structured["selection"] == "multi"
            blobs = []

            def _walk(node: object) -> None:
                if isinstance(node, dict):
                    if isinstance(node.get("content_base64"), str):
                        blobs.append(node["content_base64"])
                    for value in node.values():
                        _walk(value)
                elif isinstance(node, list):
                    for value in node:
                        _walk(value)

            _walk(structured)
            assert blobs
            assert base64.b64decode(blobs[0]) == raw
    finally:
        for key in list(_store):
            if key not in before:
                _store.pop(key, None)


def test_bounded_document_read_multi_still_passes_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two small documents keep the inline sparse payload and store nothing."""
    from response_size_guard import _store
    from tools._sparse_payload_envelope import SPARSE_POLICY_SLICE1

    projects = tmp_path / "projects"
    folder = projects / "universal-llm-gateway/notes"
    folder.mkdir(parents=True)
    (folder / "a.md").write_text("alpha\n", encoding="utf-8")
    (folder / "b.md").write_text("beta\n", encoding="utf-8")
    payload = _fs_read_multi(
        monkeypatch,
        projects,
        [
            "universal-llm-gateway/notes/a.md",
            "universal-llm-gateway/notes/b.md",
        ],
        export=True,
    )
    assert payload.get("sparse_policy") == SPARSE_POLICY_SLICE1
    before = set(_store)
    out, new_ids = _guard_fs_payload(payload)
    assert new_ids == []
    assert set(_store) == before
    assert out.structured_content["sparse_policy"] == SPARSE_POLICY_SLICE1
    assert out.structured_content["items"][0]["content"] == "alpha\n"
