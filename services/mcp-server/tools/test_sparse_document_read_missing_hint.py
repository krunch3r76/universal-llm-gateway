"""md_read / read_section miss payloads expose nearest_ids and id_format_hint."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP
from markdown_sections import parse_sections

from tools._document_manifest import _assign_section_ids
from tools._sparse_document_read import (
    _ID_FORMAT_HINT,
    _nearest_section_ids,
)
from tools.markdown_tool import register_markdown_tools

SETUP_DOC = "# Setup\n\n## Install\n\ninstall steps\n\n## Config\n\nconfig notes\n"


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("sparse-miss-hint")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    fn = next(t for t in tools if t.name == "markdown").fn
    return fn, tmp_path


def _read_section(fn, root: Path, section: str) -> dict:
    root.joinpath("setup.md").write_text(SETUP_DOC, encoding="utf-8")
    return fn(
        op="read_section",
        path="setup.md",
        sandbox="cortex",
        section=section,
    )


def test_missing_section_typo_suggests_closest_id(md_tool) -> None:
    fn, root = md_tool
    res = _read_section(fn, root, "Setup/Instal")
    assert res["ok"] is False
    assert res["outcome"] == "missing"
    assert res["error"].startswith("Section not found:")
    assert res["nearest_ids"][0] == "Setup/Install"
    assert res["id_format_hint"] == _ID_FORMAT_HINT
    assert len(res["nearest_ids"]) <= 5


def test_nearest_ids_ranks_last_path_segment_for_install() -> None:
    """Bare ``Install`` resolves on the ok path; ranking still surfaces ``Setup/Install``."""
    atx = [s for s in parse_sections(SETUP_DOC) if s.level > 0]
    indexed = _assign_section_ids(atx)
    assert "Setup/Install" in _nearest_section_ids("Install", indexed)


def test_missing_section_leaf_typo_in_miss_payload(md_tool) -> None:
    fn, root = md_tool
    res = _read_section(fn, root, "install")
    assert res["outcome"] == "missing"
    assert "Setup/Install" in res["nearest_ids"]
    assert res["id_format_hint"] == _ID_FORMAT_HINT


def test_missing_section_no_matches_still_carries_hint(md_tool) -> None:
    fn, root = md_tool
    res = _read_section(fn, root, "zzz-no-such")
    assert res["outcome"] == "missing"
    assert res["nearest_ids"] == []
    assert res["id_format_hint"] == _ID_FORMAT_HINT


def test_duplicate_ambiguous_lists_colliding_ids_in_nearest_ids(md_tool) -> None:
    fn, root = md_tool
    dup = "# A\n\nfirst\n\n# A\n\nsecond\n"
    root.joinpath("dup.md").write_text(dup, encoding="utf-8")
    res = fn(op="read_section", path="dup.md", sandbox="cortex", section="A")
    assert res.get("code") == "duplicate_ambiguous"
    assert set(res["nearest_ids"]) == {"A#1", "A#2"}
    assert res["id_format_hint"] == _ID_FORMAT_HINT
