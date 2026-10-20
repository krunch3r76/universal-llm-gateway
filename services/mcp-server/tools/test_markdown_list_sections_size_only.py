"""Size-only ``list_sections`` — total_chars without heading enumeration.

Large journals (many level-2 headings) previously forced callers to ingest the
full section tree just to learn ``total_chars``. ``size_only=True`` skips
``md_list_sections`` parsing so the response stays bounded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools.markdown_tool import register_markdown_tools


def _many_level2_doc(heading_count: int) -> str:
    parts = ["# Journal\n\n"]
    for i in range(heading_count):
        parts.append(
            f"## Entry stub {i:04d}\n\n"
            f"Reflective body for entry {i} with enough text to add weight.\n\n"
        )
    return "".join(parts)


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-markdown-size-only")
    register_markdown_tools(mcp)
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    fn = next(t for t in tools if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_size_only_skips_section_list_and_stays_under_1kb(md_tool) -> None:
    fn, root = md_tool
    text = _many_level2_doc(250)
    _write(root, "journal.md", text)

    res = fn(
        op="list_sections",
        path="journal.md",
        sandbox="cortex",
        size_only=True,
    )

    assert res["total_chars"] == len(text)
    assert res.get("read_sha256")
    assert "sections" not in res
    assert len(json.dumps(res, sort_keys=True).encode()) < 1024


def test_full_section_list_when_size_only_off(md_tool) -> None:
    fn, root = md_tool
    text = _many_level2_doc(80)
    _write(root, "journal.md", text)

    res = fn(
        op="list_sections",
        path="journal.md",
        sandbox="cortex",
        size_only=False,
    )

    assert res["total_chars"] == len(text)
    sections = res["sections"]
    assert len(sections) == 81  # one h1 + 80 h2 rows
    assert all("heading" in row and "path" in row for row in sections)
    assert len(json.dumps(res, sort_keys=True).encode()) > 1024
