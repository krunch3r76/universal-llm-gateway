"""S1b falsifiers: md_list manifest shape, paging, parent graph, preamble id."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools._document_manifest import (
    MANIFEST_MAX_DESCRIPTOR_JSON_BYTES,
    build_document_manifest,
)
from tools._hashing import sha256_hex_of_bytes
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline


def _many_level2_doc(heading_count: int, *, prefix: str = "") -> str:
    parts = [prefix, "# Journal\n\n"]
    for i in range(heading_count):
        parts.append(
            f"## Entry stub {i:04d}\n\n"
            f"Reflective body for entry {i} with enough text to add weight.\n\n"
        )
    return "".join(parts)


def _slash_heading_doc() -> str:
    lines = ["# Root\n\n"]
    for i in range(46):
        title = f"Sec {i}"
        if i < 7:
            title = f"Alpha/Beta\\/{i}"
        lines.append(f"## {title}\n\nbody {i}\n\n")
    return "".join(lines)


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-md-list-s1b")
    register_markdown_tools(mcp)
    fn = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "markdown").fn
    return fn, tmp_path


def _list(fn, root: Path, name: str, **kwargs) -> dict:
    (root / name).write_text(kwargs.pop("_text"), encoding="utf-8")
    return fn(op="list_sections", path=name, sandbox="cortex", **kwargs)


def _read(fn, root: Path, name: str, section: str, *, text: str | None = None) -> dict:
    if text is not None:
        (root / name).write_text(text, encoding="utf-8")
    return fn(op="read_section", path=name, sandbox="cortex", section=section)


def test_md_list_matches_md_read_default_manifest(md_tool) -> None:
    fn, root = md_tool
    text = "intro\n\n# Title\n\n## Child\n\nnested\n"
    listed = _list(fn, root, "doc.md", _text=text)
    read_manifest = _read(fn, root, "doc.md", "", text=text)
    assert listed["sections"] == read_manifest["sections"]
    assert listed.get("selection") == "manifest"


def test_every_listed_id_opens_on_md_read(md_tool) -> None:
    fn, root = md_tool
    text = "pre\n\n# A\n\none\n\n# A\n\ntwo\n\n# Slash\\/X\n\nok\n"
    listed = _list(fn, root, "mix.md", _text=text)
    for row in listed["sections"]:
        sid = row["id"]
        out = _read(fn, root, "mix.md", sid, text=text)
        assert out.get("outcome") == "ok", sid
    assert "__preamble__" in {r["id"] for r in listed["sections"]}


def test_preamble_miss_lists_nearest_preamble_id(md_tool) -> None:
    fn, root = md_tool
    text = "pre\n\n# Title\n\nbody\n"
    out = _read(fn, root, "p.md", "[Preamble]", text=text)
    assert out.get("outcome") == "missing"
    assert "__preamble__" in out.get("nearest_ids", [])


def test_md_list_rows_have_no_path_key(md_tool) -> None:
    fn, root = md_tool
    listed = _list(fn, root, "d.md", _text="# H\n\nx\n")
    for row in listed["sections"]:
        assert "path" not in row
        assert "id" in row


def test_parent_graph_slash_headings(md_tool) -> None:
    fn, root = md_tool
    text = _slash_heading_doc()
    listed = _list(fn, root, "slash.md", _text=text)
    rows = listed["sections"]
    by_id = {r["id"]: r for r in rows}
    for row in rows:
        pid = row.get("parent_id")
        if pid is None:
            continue
        assert pid in by_id, (row["id"], pid)
        assert row["id"] in by_id[pid]["children"]


def test_descriptor_paging_union_complete(md_tool) -> None:
    fn, root = md_tool
    text = _many_level2_doc(250)
    page0 = _list(fn, root, "big.md", _text=text)
    assert page0.get("truncated") is True
    cursor = page0.get("cursor")
    assert isinstance(cursor, int)
    seen: set[str] = set()
    page = page0
    while True:
        for row in page["sections"]:
            seen.add(row["id"])
        if not page.get("truncated") or page.get("cursor") is None:
            break
        page = fn(
            op="list_sections",
            path="big.md",
            sandbox="cortex",
            offset=page["cursor"],
        )
    digest = sha256_hex_of_bytes(text.encode("utf-8"))
    expected: set[str] = set()
    offset = 0
    while True:
        page = build_document_manifest(
            path="big.md",
            sandbox="cortex",
            text=text,
            read_sha256=digest,
            descriptor_offset=offset,
        )
        for row in page["sections"]:
            expected.add(row["id"])
        if not page.get("truncated") or page.get("cursor") is None:
            break
        offset = int(page["cursor"])
    assert seen == expected


def test_leaf_char_count_matches_content(md_tool) -> None:
    fn, root = md_tool
    text = "# Root\n\n## Child\n\nleaf only\n"
    out = _read(fn, root, "leaf.md", "Child", text=text)
    assert out["outcome"] == "ok"
    assert out["char_count"] == len(out["content"])


_SIX_BLOCK_PACKET = """---
contract: implement
---

<scope>
Implement XML navigation.
</scope>

<invariants>
- ATX unchanged
</invariants>

<task_guidance>
## Phase 1
Do the work.
</task_guidance>

<corpus>
- spec.md
</corpus>

<mcp_capabilities>
fs, cortex
</mcp_capabilities>

<output_format>
Sidecar + code.
</output_format>
"""


def test_md_list_includes_six_xml_blocks(md_tool) -> None:
    fn, root = md_tool
    listed = _list(fn, root, "packet.md", _text=_SIX_BLOCK_PACKET)
    ids = [row["id"] for row in listed["sections"] if row["id"].startswith("<")]
    assert ids == [
        "<scope>",
        "<invariants>",
        "<task_guidance>",
        "<corpus>",
        "<mcp_capabilities>",
        "<output_format>",
    ]
    assert "Phase 1" not in {row["id"] for row in listed["sections"]}


def test_preamble_not_parent_of_root_headings(md_tool) -> None:
    fn, root = md_tool
    text = "intro\n\n# Root\n\nbody\n\n## Child\n\nnested\n"
    listed = _list(fn, root, "p.md", _text=text)
    pre = next(r for r in listed["sections"] if r["id"] == "__preamble__")
    assert pre["children"] == []
    roots = [r for r in listed["sections"] if r.get("parent_id") is None]
    assert all(r["id"] != "__preamble__" or r["id"] == "__preamble__" for r in roots)
    root_row = next(r for r in listed["sections"] if r["heading"] == "Root")
    assert root_row.get("parent_id") is None


def test_empty_section_write_refused(md_tool) -> None:
    fn, root = md_tool
    (root / "w.md").write_text("T preamble line\n\n# Title\n\n", encoding="utf-8")
    out = fn(
        op="replace_section",
        path="w.md",
        sandbox="cortex",
        section="",
        target="T",
        content="x",
    )
    assert "error" in out
    assert "target" not in (out.get("reason") or "")
    for op in ("append_section", "delete_section", "rewrite_section"):
        out = fn(op=op, path="w.md", sandbox="cortex", section="", content="x")
        assert "error" in out, op


def test_f4_slash_parent_id_in_manifest(md_tool) -> None:
    fn, root = md_tool
    text = "# Parent\n\n## Alpha/Beta\\Gamma\n\nchild\n"
    listed = _list(fn, root, "slash-parent.md", _text=text)
    child = next(r for r in listed["sections"] if "Beta" in r["heading"])
    assert child["parent_id"] == "Parent"


def test_md_delete_refuses_preamble(md_tool) -> None:
    fn, root = md_tool
    (root / "x.md").write_text("pre\n\n# T\n\n", encoding="utf-8")
    out = fn(
        op="delete_section",
        path="x.md",
        sandbox="cortex",
        section="__preamble__",
    )
    assert "error" in out


def test_manifest_cap_is_32kib(md_tool) -> None:
    assert MANIFEST_MAX_DESCRIPTOR_JSON_BYTES == 32 * 1024
    fn, root = md_tool
    heavy = _many_level2_doc(120, prefix="y" * 400 + "\n\n")
    listed = _list(fn, root, "cap.md", _text=heavy)
    encoded = json.dumps(listed["sections"], ensure_ascii=False).encode()
    assert len(encoded) <= MANIFEST_MAX_DESCRIPTOR_JSON_BYTES
