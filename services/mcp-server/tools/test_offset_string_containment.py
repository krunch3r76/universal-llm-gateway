"""Non-lean offset containment: digit strings coerce, cursors do not slice.

Pins the fs_impl chokepoint and the direct markdown list path. Lean md_list
cursors stay strings; read and non-lean md_list never see a str offset.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fs_impl import fs_impl

from tools._file_helpers import read_file_result
from tools.filesystem._files_dispatcher import dispatch_files_op
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline


def _fs_kwargs(**extra: object) -> dict:
    base: dict = {
        "surface": "code",
        "overflow_registry": {},
        "sandbox": "cortex",
        "path": "notes/window.py",
        "paths": None,
        "content": "",
        "target": "",
        "target_sandbox": "",
        "line": 0,
        "section": "",
        "all_occurrences": False,
        "include_untracked": True,
        "binary": False,
        "max_depth": 3,
        "offset": 0,
        "limit": 0,
        "expected_sha256": "",
        "if_absent": False,
        "heading": "",
        "level": 0,
        "position": "",
        "mode": "",
        "thread": None,
        "since": "",
        "include_worktrees": False,
        "size_only": False,
        "lean": False,
        "sections": None,
        "export": False,
        "chunk_cursor": None,
        "op": "read",
    }
    base.update(extra)
    return base


def _patch_cortex_read(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr("tools.filesystem._paths.SANDBOX_ROOT", root)
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_file_result",
        lambda path, **kwargs: read_file_result(path, root=root, **kwargs),
    )


def _md_fn(root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", root)
    monkeypatch.setattr(
        "fs_roots.fs_root_for",
        lambda _surface, root_id: root,
    )
    mcp = FastMCP("offset-string-containment")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    return next(t for t in tools if t.name == "markdown").fn


def test_read_digit_string_offset_returns_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """offset='10' on read is a line window, not a TypeError."""
    root = tmp_path / "files"
    rel = "notes/window.py"
    target = root / rel
    target.parent.mkdir(parents=True)
    lines = [f"line-{i}" for i in range(30)]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _patch_cortex_read(monkeypatch, root)
    out = fs_impl(
        **_fs_kwargs(
            overflow_registry={"files": dispatch_files_op},
            path=rel,
            offset="10",
            limit=2,
        )
    )
    assert "error" not in out
    assert out.get("code") is None
    assert "TypeError" not in str(out)
    assert out["line_range"]["offset"] == 10
    assert out["line_range"]["returned"] == 2
    assert out["content"] == "line-10\nline-11"


def test_read_lean_cursor_offset_is_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """offset='d2:4' on read returns an envelope, not a TypeError."""
    root = tmp_path / "files"
    rel = "notes/window.py"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("line-0\nline-1\n", encoding="utf-8")
    _patch_cortex_read(monkeypatch, root)
    out = fs_impl(
        **_fs_kwargs(
            overflow_registry={"files": dispatch_files_op},
            path=rel,
            offset="d2:4",
        )
    )
    assert isinstance(out, dict)
    assert out.get("code") == "invalid_cursor_scope"
    assert "TypeError" not in str(out)
    assert "lean=true" in str(out.get("message"))


def test_read_non_cursor_string_offset_is_clear_error() -> None:
    """offset='not-a-cursor' on read is an envelope, not a TypeError."""
    out = fs_impl(**_fs_kwargs(offset="not-a-cursor"))
    assert isinstance(out, dict)
    assert out.get("code") == "invalid_offset"
    assert "TypeError" not in str(out)
    assert "lean=true" in str(out.get("message"))


def test_non_lean_md_list_digit_string_pages_like_int(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """md_list without lean treats offset='5' as descriptor index 5."""
    root = tmp_path / "files"
    root.mkdir()
    text = "\n\n".join(f"# Heading {i}\n\nbody {i}" for i in range(12)) + "\n"
    (root / "page.md").write_text(text, encoding="utf-8")
    md_fn = _md_fn(root, monkeypatch)
    via_int = fs_impl(
        **_fs_kwargs(
            overflow_registry={"markdown": md_fn},
            op="md_list",
            path="page.md",
            offset=5,
            lean=False,
        )
    )
    via_str = fs_impl(
        **_fs_kwargs(
            overflow_registry={"markdown": md_fn},
            op="md_list",
            path="page.md",
            offset="5",
            lean=False,
        )
    )
    direct = md_fn(
        op="list_sections",
        path="page.md",
        sandbox="cortex",
        lean=False,
        offset="5",
    )
    assert via_int.get("code") is None and "sections" in via_int
    assert [row["id"] for row in via_str["sections"]] == [
        row["id"] for row in via_int["sections"]
    ]
    assert [row["id"] for row in direct["sections"]] == [
        row["id"] for row in via_int["sections"]
    ]
    assert via_str["sections"][0]["id"] != "heading-0"
    assert via_str["sections"][0]["heading"] == "Heading 5"
