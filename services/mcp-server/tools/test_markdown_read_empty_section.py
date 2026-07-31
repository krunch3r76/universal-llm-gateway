"""Friction 19936 — md_read empty/absent section returns the full document.

The fs ``md_read`` op defaults ``section`` to ``""``; in the text/markdown path
that value also selected the (often empty) preamble, so a bare ``md_read`` on a
heading-first doc silently returned ``{"content": ""}``. The read surface now
treats omitted/empty/whitespace section as a full-document request, while the
markdown-section write primitive keeps ``""`` = preamble and the PDF navigator
keeps its explicit empty-selector refusal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP
from markdown_sections import resolve_section

from tools._pdf_sections import PdfSectionError, _resolve
from tools.markdown_tool import register_markdown_tools

HEADING_FIRST = "# Title\n\nBody line one.\nBody line two.\n"
PREAMBLE_DOC = "Preamble paragraph.\n\n# Title\n\nSection body.\n"


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return the live ``markdown`` tool fn bound to a temp cortex root."""
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-markdown")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    fn = next(t for t in tools if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_empty_section_returns_full_document(md_tool) -> None:
    fn, root = md_tool
    _write(root, "kernel.md", HEADING_FIRST)
    res = fn(op="read_section", path="kernel.md", sandbox="cortex", section="")
    assert res["content"] == HEADING_FIRST
    assert res["section"] is None
    assert res["selection"] == "full_document"


def test_omitted_section_returns_full_document(md_tool) -> None:
    fn, root = md_tool
    _write(root, "kernel.md", HEADING_FIRST)
    res = fn(op="read_section", path="kernel.md", sandbox="cortex")
    assert res["content"] == HEADING_FIRST
    assert res["section"] is None
    assert res["selection"] == "full_document"


def test_whitespace_only_section_returns_full_document(md_tool) -> None:
    fn, root = md_tool
    _write(root, "kernel.md", HEADING_FIRST)
    res = fn(op="read_section", path="kernel.md", sandbox="cortex", section="   ")
    assert res["content"] == HEADING_FIRST
    assert res["selection"] == "full_document"


def test_preamble_present_empty_section_returns_whole_document(md_tool) -> None:
    fn, root = md_tool
    _write(root, "doc.md", PREAMBLE_DOC)
    res = fn(op="read_section", path="doc.md", sandbox="cortex", section="")
    # whole document, not merely the leading preamble paragraph
    assert res["content"] == PREAMBLE_DOC
    assert "Section body." in res["content"]
    assert res["selection"] == "full_document"


def test_real_section_unchanged_and_unmarked(md_tool) -> None:
    fn, root = md_tool
    _write(root, "doc.md", PREAMBLE_DOC)
    res = fn(op="read_section", path="doc.md", sandbox="cortex", section="Title")
    assert "Section body." in res["content"]
    assert res["section"] == "Title"
    assert "selection" not in res


def test_nonexistent_section_errors_without_fallback(md_tool) -> None:
    fn, root = md_tool
    _write(root, "doc.md", PREAMBLE_DOC)
    res = fn(op="read_section", path="doc.md", sandbox="cortex", section="Nope")
    assert "error" in res
    assert "content" not in res


def test_resolve_section_empty_still_targets_preamble_for_writes() -> None:
    # Write primitive is untouched: '' still selects the level-0 preamble.
    sec = resolve_section(PREAMBLE_DOC, "")
    assert sec.level == 0


def test_pdf_empty_selector_still_raises() -> None:
    with pytest.raises(PdfSectionError):
        _resolve([], "")
