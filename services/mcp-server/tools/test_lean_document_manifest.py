"""Acceptance matrix for opt-in lean md_list (todo:md-list-lean)."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from fastmcp import FastMCP

from tools._document_manifest import build_document_manifest
from tools._hashing import sha256_hex_of_bytes
from tools._lean_document_manifest import (
    LEAN_MAX_DESCRIPTOR_JSON_BYTES,
    build_lean_document_manifest,
)
from tools._lean_offset_codec import encode_lean_descriptor_offset
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline

_SIX_BLOCK = """---
contract: implement
---

<scope>
Implement XML navigation.
</scope>

# Title

## Child

nested
"""


def _fixture_roundtrip() -> str:
    return (
        "preamble line\n\n"
        "```\n# not a heading\n```\n\n"
        "# Dup\n\none\n\n# Dup\n\ntwo\n\n"
        "## Slash\\/Path\n\nok\n\n"
        + _SIX_BLOCK
    )


def _synthetic_reasoning_shaped(*, h2: int = 60, h3: int = 650, pad: int = 1_050_000) -> str:
    parts = ["# Long reasoning journal\n\n" + ("x" * 2000) + "\n\n"]
    for i in range(h2):
        parts.append(f"## Seat block {i:04d}\n\n" + ("y" * 120) + "\n\n")
    for j in range(h3):
        parts.append(f"### Detail {j:05d}\n\n" + ("z" * 40) + "\n\n")
    body = "".join(parts)
    if len(body) < pad:
        body += "\n" + ("w" * (pad - len(body))) + "\n"
    return body


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-md-list-lean")
    register_markdown_tools(mcp)
    import asyncio

    fn = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    return sha256_hex_of_bytes(text.encode("utf-8"))


def _list(fn, root: Path, name: str, text: str, **kwargs) -> dict:
    _write(root, name, text)
    return fn(op="list_sections", path=name, sandbox="cortex", **kwargs)


def _wire_payload_bytes(payload: dict) -> int:
    """Approximate MCP tools/call body size (text + structured duplicate)."""
    enc = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return len(enc) * 2


def test_lean_roundtrip_ids_md_read(md_tool) -> None:
    fn, root = md_tool
    text = _fixture_roundtrip()
    for depth in (1, 2, 3):
        listed = _list(fn, root, "rt.md", text, lean=True, max_depth=depth)
        for row in listed["rows"]:
            assert {"id", "level", "char_count"} <= row.keys(), (depth, row)
            sid = row["id"]
            single = fn(
                op="read_section",
                path="rt.md",
                sandbox="cortex",
                section=sid,
            )
            assert single.get("outcome") == "ok", (depth, sid, single)
            batch = fn(
                op="read_section",
                path="rt.md",
                sandbox="cortex",
                sections=[sid],
            )
            items = batch.get("items")
            assert items, (depth, sid, batch)
            assert all(item.get("outcome") == "ok" for item in items), (
                depth,
                sid,
                batch,
            )


def test_lean_parity_with_full_manifest(md_tool) -> None:
    fn, root = md_tool
    text = _fixture_roundtrip()
    digest = _write(root, "parity.md", text)
    full = build_document_manifest(
        path="parity.md",
        sandbox="cortex",
        text=text,
        read_sha256=digest,
    )
    lean = build_lean_document_manifest(
        path="parity.md",
        sandbox="cortex",
        text=text,
        read_sha256=digest,
        max_depth=6,
    )
    by_id = {r["id"]: r for r in full["sections"]}
    for row in lean["rows"]:
        full_row = by_id[row["id"]]
        assert row["id"] == full_row["id"]
        assert row["level"] == full_row["level"]
        assert row["char_count"] == full_row["char_count"]
        assert row.get("start_line") is None
        assert row.get("title") is None
        if "subtree_char_count" in row:
            assert row["subtree_char_count"] == full_row["subtree_char_count"]


def test_non_lean_md_list_unchanged(md_tool) -> None:
    fn, root = md_tool
    text = _fixture_roundtrip()
    digest = _write(root, "reg.md", text)
    direct = build_document_manifest(
        path="reg.md",
        sandbox="cortex",
        text=text,
        read_sha256=digest,
    )
    for max_depth in (3, 1):
        via_tool = fn(
            op="list_sections",
            path="reg.md",
            sandbox="cortex",
            max_depth=max_depth,
        )
        assert via_tool.get("lean") is not True
        assert json.dumps(via_tool, sort_keys=True) == json.dumps(
            build_document_manifest(
                path="reg.md",
                sandbox="cortex",
                text=text,
                read_sha256=digest,
            ),
            sort_keys=True,
        )
    assert via_tool == direct
    assert "sections" in via_tool and "rows" not in via_tool


def test_lean_budget_and_paging_synthetic(md_tool) -> None:
    fn, root = md_tool
    text = _synthetic_reasoning_shaped()
    depth1 = _list(fn, root, "syn.md", text, lean=True, max_depth=1)
    assert _wire_payload_bytes(depth1) < 8000
    depth2 = _list(fn, root, "syn.md", text, lean=True, max_depth=2)
    for depth, page in ((1, depth1), (2, depth2)):
        row_bytes = len(json.dumps(page["rows"], ensure_ascii=False).encode())
        assert row_bytes <= LEAN_MAX_DESCRIPTOR_JSON_BYTES, depth
    seen: set[str] = set()
    page = depth2
    while True:
        for row in page["rows"]:
            assert row["id"] not in seen
            seen.add(row["id"])
        if not page.get("truncated") or page.get("cursor") is None:
            break
        page = fn(
            op="list_sections",
            path="syn.md",
            sandbox="cortex",
            lean=True,
            max_depth=2,
            offset=page["cursor"],
        )
    depth3 = _list(fn, root, "syn3.md", text, lean=True, max_depth=3)
    page = depth3
    while page.get("truncated") and page.get("cursor") is not None:
        enc = json.dumps(page["rows"], ensure_ascii=False).encode()
        assert len(enc) <= LEAN_MAX_DESCRIPTOR_JSON_BYTES
        page = fn(
            op="list_sections",
            path="syn3.md",
            sandbox="cortex",
            lean=True,
            max_depth=3,
            offset=page["cursor"],
        )


def test_lean_single_parse(md_tool, monkeypatch: pytest.MonkeyPatch) -> None:
    import markdown_sections as ms

    calls = {"n": 0}
    real = ms.parse_sections

    def counted(text: str):
        calls["n"] += 1
        return real(text)

    monkeypatch.setattr(ms, "parse_sections", counted)
    try:
        fn, root = md_tool
        text = _synthetic_reasoning_shaped(h2=40, h3=120, pad=200_000)
        _list(fn, root, "once.md", text, lean=True, max_depth=2)
        assert calls["n"] == 1
    finally:
        monkeypatch.setattr(ms, "parse_sections", real)


def test_lean_max_depth_level_cutoff(md_tool) -> None:
    fn, root = md_tool
    text = "# R\n\n## A\n\n### B\n\n#### C\n\nd\n"
    d1 = _list(fn, root, "depth.md", text, lean=True, max_depth=1)
    assert {r["level"] for r in d1["rows"]} <= {0, 1}
    d2 = _list(fn, root, "depth.md", text, lean=True, max_depth=2)
    assert any(r["level"] == 2 for r in d2["rows"])
    assert all(r["level"] <= 2 for r in d2["rows"])
    bad_low = _list(fn, root, "depth.md", text, lean=True, max_depth=0)
    assert bad_low.get("code") == "invalid_max_depth"
    bad_high = _list(fn, root, "depth.md", text, lean=True, max_depth=9)
    assert bad_high.get("code") == "invalid_max_depth"


def test_lean_pdf_not_applied(md_tool) -> None:
    fn, root = md_tool
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Alpha")
    doc.set_toc([[1, "Alpha", 1]])
    doc.save(str(root / "x.pdf"))
    doc.close()
    out = fn(
        op="list_sections",
        path="x.pdf",
        sandbox="cortex",
        lean=True,
    )
    assert out.get("lean_applied") is False
    assert out.get("lean_requested") is True
    assert out.get("sections")


def test_lean_size_only_conflict(md_tool) -> None:
    fn, root = md_tool
    text = "# T\n\nx\n"
    out = _list(fn, root, "c.md", text, lean=True, size_only=True)
    assert out.get("code") == "invalid_selector_combo"


def test_lean_cursor_scope(md_tool) -> None:
    fn, root = md_tool
    text = _synthetic_reasoning_shaped(h2=80, h3=5, pad=50_000)
    page0 = _list(fn, root, "cur.md", text, lean=True, max_depth=2)
    assert page0.get("truncated")
    cursor = page0["cursor"]
    assert isinstance(cursor, str) and cursor.startswith("d2:")
    bad = fn(
        op="list_sections",
        path="cur.md",
        sandbox="cortex",
        lean=True,
        max_depth=3,
        offset=cursor,
    )
    assert bad.get("code") == "invalid_cursor_scope"
    no_lean = fn(
        op="list_sections",
        path="cur.md",
        sandbox="cortex",
        lean=False,
        offset=cursor,
    )
    assert no_lean.get("code") == "invalid_cursor_scope"


def test_lean_cursor_continuation_without_scope_param(md_tool) -> None:
    fn, root = md_tool
    text = _synthetic_reasoning_shaped(h2=60, h3=8, pad=60_000)
    first = _list(fn, root, "enc.md", text, lean=True, max_depth=2)
    assert first.get("truncated")
    cursor = first["cursor"]
    second = fn(
        op="list_sections",
        path="enc.md",
        sandbox="cortex",
        lean=True,
        max_depth=2,
        offset=cursor,
    )
    assert second.get("code") is None
    assert second.get("rows")
    ids_first = {r["id"] for r in first["rows"]}
    ids_second = {r["id"] for r in second["rows"]}
    assert ids_first.isdisjoint(ids_second)


def test_lean_cursor_forged_and_bare_int(md_tool) -> None:
    fn, root = md_tool
    text = "# A\n\n## B\n\nc\n"
    _list(fn, root, "forg.md", text, lean=True, max_depth=2)
    forged = fn(
        op="list_sections",
        path="forg.md",
        sandbox="cortex",
        lean=True,
        max_depth=2,
        offset="not-a-cursor",
    )
    assert forged.get("code") == "invalid_cursor_scope"
    mismatch = fn(
        op="list_sections",
        path="forg.md",
        sandbox="cortex",
        lean=True,
        max_depth=2,
        offset=encode_lean_descriptor_offset(1, 5),
    )
    assert mismatch.get("code") == "invalid_cursor_scope"
    bare = fn(
        op="list_sections",
        path="forg.md",
        sandbox="cortex",
        lean=True,
        max_depth=2,
        offset=3,
    )
    assert bare.get("code") == "invalid_cursor_scope"
