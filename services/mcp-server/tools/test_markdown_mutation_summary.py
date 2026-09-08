"""Friction 21874 — md_replace patch-only; md_rewrite_section shrink warnings."""

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


def test_md_replace_patches_without_truncating_section(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    res = fn(
        op="replace_section",
        path="charter.md",
        sandbox="cortex",
        section="Lessons",
        target="- lesson two\n",
        content="- lesson two (revised)\n",
    )
    assert res["status"] == "replaced"
    assert res["patch_mode"] is True
    assert res["replacements_made"] == 1
    text = (root / "charter.md").read_text(encoding="utf-8")
    assert "- lesson one\n" in text
    assert "- lesson two (revised)\n" in text
    assert "- lesson six\n" in text
    assert "_warning" not in res


def test_md_replace_without_target_refuses(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    before = (root / "charter.md").read_text(encoding="utf-8")
    res = fn(
        op="replace_section",
        path="charter.md",
        sandbox="cortex",
        section="Lessons",
        content="- one new bullet only\n",
    )
    assert "error" in res
    assert res["reason"] == "md_replace.target_required"
    assert (root / "charter.md").read_text(encoding="utf-8") == before


def test_md_rewrite_section_warns_on_accidental_truncation(md_tool) -> None:
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    res = fn(
        op="rewrite_section",
        path="charter.md",
        sandbox="cortex",
        section="Lessons",
        content="- one new bullet only\n",
    )
    assert res["status"] == "rewritten"
    mutation = res["mutation"]
    assert mutation["prior_body_lines"] > mutation["new_body_lines"]
    assert mutation["size_delta_ratio"] < -0.5
    assert "_warning" in res


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


def test_md_insert_at_end_succeeds_without_write_then_error(md_tool) -> None:
    """Friction a:32587 — insert must not write then fail summary on new heading."""
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    before = (root / "charter.md").read_text(encoding="utf-8")
    res = fn(
        op="insert_section",
        path="charter.md",
        sandbox="cortex",
        heading="Follow Up",
        level=2,
        position="end",
        content="New follow-up item.\n",
    )
    assert "error" not in res
    assert res["status"] == "inserted"
    assert res["section"] == "Follow Up"
    after = (root / "charter.md").read_text(encoding="utf-8")
    assert after != before
    assert "## Follow Up" in after
    assert "New follow-up item." in after
    mutation = res["mutation"]
    assert mutation["prior_body_chars"] == 0
    assert mutation["new_body_chars"] > 0


def test_md_insert_bad_anchor_errors_without_partial_write(md_tool) -> None:
    """Friction a:32581 — unresolved anchor must not persist a partial insert."""
    fn, root = md_tool
    _write(root, "charter.md", DOC)
    before = (root / "charter.md").read_text(encoding="utf-8")
    res = fn(
        op="insert_section",
        path="charter.md",
        sandbox="cortex",
        heading="Orphan",
        level=2,
        position="after",
        section="No Such Section",
        content="should not land\n",
    )
    assert "error" in res
    assert "Section not found" in res["error"]
    assert (root / "charter.md").read_text(encoding="utf-8") == before
