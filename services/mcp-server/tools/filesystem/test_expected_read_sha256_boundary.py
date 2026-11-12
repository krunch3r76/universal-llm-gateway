"""S0 a:43743 — coerce expected_read_sha256 maps before markdown dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP

from fs_impl import fs_impl
from tools._hashing import sha256_hex_of_file
from tools.filesystem._expected_read_sha256_boundary import (
    coerce_expected_read_sha256_for_md,
)
from tools.markdown_tool import register_markdown_tools

pytestmark = pytest.mark.offline


def _md_registry(root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools.markdown_tool._FILES_ROOT", root)
    monkeypatch.setattr(
        "fs_roots.fs_root_for",
        lambda _surface, root_id: root,
    )
    mcp = FastMCP("expected-read-sha256-boundary")
    register_markdown_tools(mcp)
    tools = asyncio.run(mcp.list_tools())
    md_fn = next(t for t in tools if t.name == "markdown").fn
    return md_fn


def _fs_kwargs(md_fn, **extra):
    base = {
        "surface": "code",
        "overflow_registry": {"markdown": md_fn},
        "sandbox": "cortex",
        "path": "notes/doc.md",
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
        "sections": None,
        "export": False,
        "chunk_cursor": None,
    }
    base.update(extra)
    return base


def test_md_read_matching_dict_returns_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(
            md_fn,
            op="md_read",
            section="Title",
            expected_read_sha256={rel: live},
        )
    )
    assert "error" not in out, out
    assert out.get("code") != "param_not_applicable", out
    assert out.get("selection") == "section"
    assert "body" in out.get("content", "")


def test_md_read_non_matching_dict_returns_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(
            md_fn,
            op="md_read",
            expected_read_sha256={"other.md": "a" * 64},
        )
    )
    assert out.get("code") == "param_not_applicable"
    assert "other.md" in out["data"]["keys"]
    assert "AttributeError" not in str(out)


def test_md_to_dict_with_dict_returns_param_not_applicable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(
            md_fn,
            op="md_to_dict",
            expected_read_sha256={rel: live},
        )
    )
    assert out.get("code") == "param_not_applicable"
    assert out["data"]["parameter"] == "expected_read_sha256"


def test_read_multi_dict_unchanged_at_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.filesystem._ops_text import read_files_batch_impl

    projects = tmp_path / "projects"
    rel = "universal-llm-gateway/notes/doc.md"
    target = projects / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: projects)
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_files_batch_impl",
        read_files_batch_impl,
    )
    uri = "workspaces://universal-llm-gateway/notes/doc.md"
    out = fs_impl(
        surface="code",
        overflow_registry={},
        op="read_multi",
        sandbox="workspaces",
        path="",
        paths=[rel],
        content="",
        target="",
        target_sandbox="",
        line=0,
        section="",
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=0,
        expected_sha256="",
        if_absent=False,
        heading="",
        level=0,
        position="",
        mode="",
        thread=None,
        since="",
        include_worktrees=False,
        size_only=False,
        sections=["Title"],
        export=False,
        expected_read_sha256={uri: live},
        chunk_cursor=None,
    )
    assert out.get("code") != "param_not_applicable", out
    item = out["files"][rel]["items"][0]
    assert item["outcome"] == "ok"


def test_coerce_unit_matching_share_uri() -> None:
    digest, err = coerce_expected_read_sha256_for_md(
        op="md_read",
        expected={"cortex://notes/x.md": "ab" * 32},
        path="notes/x.md",
        sandbox="cortex",
    )
    assert err is None
    assert digest == "ab" * 32
