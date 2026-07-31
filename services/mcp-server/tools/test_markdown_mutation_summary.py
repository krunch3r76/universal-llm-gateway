"""Friction 21874 — md_replace/md_delete return mutation summaries + shrink warnings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools.markdown_tool import register_markdown_tools

DOC = (
    "# Charter\n\n"
    "## Lessons\n\n"
    "- lesson one\n"
    "- lesson two\n"
    "- lesson three\n"
    "- lesson four\n"
    "- lesson five\n"
    "- lesson six\n\n"
    "## Front Line\n\n"
    "Existing front-line paragraph with substantial context.\n"
    "Second line of context.\n"
    "Third line of context.\n"
)


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-markdown-mutation")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    fn = next(t for t in tools if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def test_md_replace_reports_mutation_without_warning_on_small_edit(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    res = fn(
        op="replace_section",
        path="charter.md",
        sandbox="cortex",
        section="Lessons",
        content="- lesson one\n- lesson two\n- lesson three\n- lesson four\n- lesson five\n- lesson six\n- lesson seven\n",
    )
    assert res["status"] == "replaced"
    assert "mutation" in res
    assert res["mutation"]["prior_body_lines"] >= 6
    assert res["mutation"]["new_body_lines"] >= res["mutation"]["prior_body_lines"]
    assert "_warning" not in res


def test_md_replace_warns_on_accidental_truncation(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    res = fn(
        op="replace_section",
        path="charter.md",
        sandbox="cortex",
        section="Lessons",
        content="- one new bullet only\n",
    )
    assert res["status"] == "replaced"
    mutation = res["mutation"]
    assert mutation["prior_body_lines"] > mutation["new_body_lines"]
    assert mutation["lines_removed"] > 0
    assert mutation["size_delta_ratio"] < -0.5
    assert "_warning" in res
    assert "md_append" in res["_warning"]


def test_md_delete_reports_removed_body(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    res = fn(
        op="delete_section",
        path="charter.md",
        sandbox="cortex",
        section="Front Line",
    )
    assert res["status"] == "deleted"
    mutation = res["mutation"]
    assert mutation["deleted_body_lines"] >= 3
    assert mutation["deleted_body_chars"] >= 80
    assert mutation["new_body_chars"] == 0
    assert "_warning" in res
