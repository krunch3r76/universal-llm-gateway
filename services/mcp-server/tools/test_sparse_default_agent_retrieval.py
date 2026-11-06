"""Sparse default agent retrieval — document manifest and section batch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools._file_helpers import read_file_result, read_files_batch
from tools._hashing import sha256_hex_of_bytes
from tools.markdown_tool import register_markdown_tools


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    monkeypatch.setattr("tools._file_helpers.FILES_ROOT", tmp_path)
    mcp = FastMCP("sparse-md")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    fn = next(t for t in tools if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    return sha256_hex_of_bytes(text.encode("utf-8"))


def test_manifest_omitted_section(md_tool) -> None:
    fn, root = md_tool
    digest = _write(root, "a.md", "# One\n\nbody\n")
    res = fn(op="read_section", path="a.md", sandbox="cortex")
    assert res["selection"] == "manifest"
    assert "content" not in res
    assert res["read_sha256"] == digest


def test_sections_batch_two_ids(md_tool) -> None:
    fn, root = md_tool
    text = "# A\n\nalpha\n\n# B\n\nbeta\n"
    digest = _write(root, "batch.md", text)
    res = fn(
        op="read_section",
        path="batch.md",
        sandbox="cortex",
        sections=["A", "B"],
    )
    assert res["read_sha256"] == digest
    assert [i["id"] for i in res["items"]] == ["A", "B"]
    assert "alpha" in res["items"][0]["content"]
    assert "beta" in res["items"][1]["content"]


def test_duplicate_path_ambiguous(md_tool) -> None:
    fn, root = md_tool
    _write(root, "dup.md", "# A\n\nfirst\n\n# A\n\nsecond\n")
    res = fn(op="read_section", path="dup.md", sandbox="cortex", section="A")
    assert res.get("code") == "duplicate_ambiguous" or (
        res.get("items") and res["items"][0]["outcome"] == "duplicate_ambiguous"
    )
    only_second = fn(
        op="read_section",
        path="dup.md",
        sandbox="cortex",
        section="A#2",
    )
    assert "second" in only_second["content"]


def test_export_conflict(md_tool) -> None:
    fn, root = md_tool
    _write(root, "x.md", "# T\n\nx\n")
    res = fn(
        op="read_section",
        path="x.md",
        sandbox="cortex",
        export=True,
        sections=["T"],
    )
    assert res["code"] == "invalid_selector_combo"


def test_fs_read_py_still_returns_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools._file_helpers.FILES_ROOT", tmp_path)
    monkeypatch.setattr("tools.filesystem._paths.SANDBOX_ROOT", tmp_path)
    (tmp_path / "mod.py").write_text("print('hi')\n", encoding="utf-8")
    out = read_file_result("mod.py", root=tmp_path)
    assert "content" in out


def test_read_files_batch_manifest_fail_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools._file_helpers.FILES_ROOT", tmp_path)
    (tmp_path / "doc.md").write_text("# T\n\nbody\n", encoding="utf-8")
    batch = read_files_batch(["doc.md"], root=tmp_path)
    entry = batch["doc.md"]
    assert isinstance(entry, dict)
    assert entry.get("code") == "sparse_manifest_requires_expansion"
    assert entry.get("selection") == "manifest"


def test_parent_body_excludes_child(md_tool) -> None:
    fn, root = md_tool
    _write(
        root,
        "parent.md",
        "# Parent\n\nPARENT_ONLY\n\n## Child\n\nCHILD_ONLY\n",
    )
    res = fn(op="read_section", path="parent.md", sandbox="cortex", section="Parent")
    assert "PARENT_ONLY" in res["content"]
    assert "CHILD_ONLY" not in res["content"]


def test_parent_body_fence_hash_not_child_heading(md_tool) -> None:
    fn, root = md_tool
    _write(
        root,
        "fence.md",
        "# Parent\n\nbefore fence\n\n```\n# FakeChild\n\ninside\n```\n\n## Child\n\nafter\n",
    )
    res = fn(op="read_section", path="fence.md", sandbox="cortex", section="Parent")
    assert "before fence" in res["content"]
    assert "# FakeChild" in res["content"]
    assert "inside" in res["content"]
    assert "after" not in res["content"]


def test_unsectioned_expand_full_body(md_tool) -> None:
    fn, root = md_tool
    text = "plain body without headings\n"
    _write(root, "plain.md", text)
    manifest = fn(op="read_section", path="plain.md", sandbox="cortex")
    assert manifest["sections"][0]["id"] == "__unsectioned__"
    expanded = fn(
        op="read_section",
        path="plain.md",
        sandbox="cortex",
        sections=["__unsectioned__"],
    )
    assert expanded["items"][0]["outcome"] == "ok"
    assert expanded["items"][0]["content"] == text
    assert expanded.get("sparse_section_batch") is True


def test_sections_over_32kb_single_section_ok(md_tool) -> None:
    fn, root = md_tool
    big = "z" * 35_000
    _write(root, "large.md", f"# Big\n\n{big}\n")
    res = fn(
        op="read_section",
        path="large.md",
        sandbox="cortex",
        sections=["Big"],
    )
    assert res["items"][0]["outcome"] == "ok"
    assert len(res["items"][0]["content"]) > 32_768
    assert res.get("selection") == "sections"
