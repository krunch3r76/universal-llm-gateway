"""md_to_dict returns lean-metadata nesting without section bodies (todo:md-to-dict-no-bodies)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools._lean_to_dict import lean_heading_tree_dict
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline

_LONG_BODY = (
    "UNIQUE_MD_TO_DICT_BODY_MARKER_25129 "
    + ("paragraph filler. " * 400)
)

_META_KEYS = frozenset({"id", "level", "char_count", "subtree_char_count", "child_count"})


@pytest.fixture
def md_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", tmp_path)
    mcp = FastMCP("test-md-to-dict")
    register_markdown_tools(mcp)
    import asyncio

    fn = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "markdown").fn
    return fn, tmp_path


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


def _collect_strings(node: object) -> list[str]:
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _META_KEYS and isinstance(v, str):
                out.append(v)
            elif k not in _META_KEYS:
                out.extend(_collect_strings(v))
    return out


def _find_node_by_id(tree: dict, target_id: str) -> dict | None:
    if tree.get("id") == target_id:
        return tree
    for key, val in tree.items():
        if key in _META_KEYS or not isinstance(val, dict):
            continue
        found = _find_node_by_id(val, target_id)
        if found is not None:
            return found
    return None


def test_md_to_dict_fixture_has_no_body_strings(md_tool) -> None:
    fn, root = md_tool
    text = f"# Title\n\n{_LONG_BODY}\n\n## Child\n\nshort\n"
    _write(root, "long.md", text)
    out = fn(op="to_dict", path="long.md", sandbox="cortex")
    assert "error" not in out, out
    payload = json.dumps(out, ensure_ascii=False)
    assert "UNIQUE_MD_TO_DICT_BODY_MARKER_25129" not in payload
    for s in _collect_strings(out.get("data") or {}):
        assert len(s) < 200, s[:80]


def test_md_to_dict_ids_match_lean_md_list(md_tool) -> None:
    fn, root = md_tool
    text = (
        "preamble\n\n"
        "# Root\n\nbody\n\n"
        "## Alpha\n\na\n\n"
        "### Detail\n\nd\n\n"
        "## Beta\n\nb\n"
    )
    _write(root, "tree.md", text)
    listed = fn(
        op="list_sections",
        path="tree.md",
        sandbox="cortex",
        lean=True,
        max_depth=6,
    )
    assert listed.get("lean") is True, listed
    to_dict = fn(op="to_dict", path="tree.md", sandbox="cortex")
    data = to_dict.get("data") or {}
    for row in listed["rows"]:
        sid = row["id"]
        node = _find_node_by_id(data, sid)
        assert node is not None, (sid, data)
        assert node.get("id") == sid
        assert node.get("level") == row["level"]
        assert node.get("char_count") == row["char_count"]
    alpha_id = next(r["id"] for r in listed["rows"] if r.get("level") == 2)
    read = fn(
        op="read_section",
        path="tree.md",
        sandbox="cortex",
        section=alpha_id,
    )
    assert read.get("outcome") == "ok", read


def test_md_to_dict_playbook_under_8kb(md_tool) -> None:
    fn, root = md_tool
    # Production-scale outline (many headings); bodies are omitted from the tree.
    parts = ["# Playbook root\n\nintro\n\n"]
    for i in range(32):
        parts.append(f"## Section {i:02d}\n\n" + ("x" * 1200) + "\n\n")
        parts.append(f"### Sub {i}a\n\n" + ("y" * 600) + "\n\n")
    text = "".join(parts)
    _write(root, "big.md", text)
    out = fn(op="to_dict", path="big.md", sandbox="cortex")
    wire = json.dumps(out, ensure_ascii=False).encode("utf-8")
    assert len(wire) < 8 * 1024, len(wire)
    assert "x" * 50 not in wire.decode()


def test_lean_heading_tree_matches_direct_helper() -> None:
    text = "# A\n\naaa\n\n## B\n\nbbb\n"
    built = lean_heading_tree_dict(text, max_depth=6)
    direct = built["tree"]
    assert built["max_depth_applied"] == 6
    assert "A" in direct
    assert direct["A"]["id"] == "A"
    assert isinstance(direct["A"].get("B"), dict)


def test_md_to_dict_duplicate_sibling_titles_kept(md_tool) -> None:
    fn, root = md_tool
    text = "# Parent\n\n# Dup\n\none\n\n# Dup\n\ntwo\n"
    _write(root, "dup.md", text)
    out = fn(op="to_dict", path="dup.md", sandbox="cortex")
    data = out.get("data") or {}
    assert _find_node_by_id(data, "Dup#1") is not None
    assert _find_node_by_id(data, "Dup#2") is not None


def test_md_to_dict_heading_titled_id_does_not_clobber_metadata(md_tool) -> None:
    fn, root = md_tool
    text = "# Root\n\nbody\n\n## id\n\nchild\n"
    _write(root, "meta.md", text)
    out = fn(op="to_dict", path="meta.md", sandbox="cortex")
    data = out.get("data") or {}
    root_node = data["Root"]
    assert root_node.get("id") == "Root"
    child = root_node.get("Root/id") or _find_node_by_id(data, "Root/id")
    assert child is not None
    assert child.get("id") == "Root/id"


def test_md_to_dict_max_depth_explicit_three_cuts_tree(md_tool) -> None:
    fn, root = md_tool
    text = "# Top\n\n## L2\n\na\n\n### L3\n\nd\n"
    _write(root, "depth.md", text)
    shallow = fn(op="to_dict", path="depth.md", sandbox="cortex", max_depth=3)
    deep = fn(op="to_dict", path="depth.md", sandbox="cortex", max_depth=2)
    assert shallow.get("max_depth_applied") == 3
    assert deep.get("max_depth_applied") == 2
    assert deep.get("depth_rows_omitted", 0) >= 1
    assert _find_node_by_id(deep.get("data") or {}, "Top/L2/L3") is None
    assert _find_node_by_id(shallow.get("data") or {}, "Top/L2/L3") is not None


def test_md_to_dict_default_max_depth_six_includes_deep(md_tool) -> None:
    fn, root = md_tool
    text = "# Top\n\n## L2\n\na\n\n### L3\n\nd\n"
    _write(root, "deep.md", text)
    out = fn(op="to_dict", path="deep.md", sandbox="cortex")
    assert out.get("max_depth_applied") == 6
    assert out.get("depth_rows_omitted") == 0
    assert _find_node_by_id(out.get("data") or {}, "Top/L2/L3") is not None
