"""Markdown/converted-document offset/limit window gate (manifest-first)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tools._file_helpers import read_file_result
from tools._fs_read import bind_fs_read_overflow, register_fs_read_tool
from tools._hashing import sha256_hex_of_file


@pytest.fixture
def sandbox_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("tools._file_helpers.FILES_ROOT", tmp_path)
    monkeypatch.setattr("tools.filesystem._paths.SANDBOX_ROOT", tmp_path)
    return tmp_path


def _closeout_body() -> str:
    lines = ["status: complete", "line two", ""]
    lines.append("## effects_manifest")
    lines.extend(f"effect {i}" for i in range(5))
    return "\n".join(lines)


def test_md_window_without_hash_returns_gated_manifest(sandbox_root: Path) -> None:
    rel = "tmp/reviews/closeouts/d-1.md"
    (sandbox_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (sandbox_root / rel).write_text(_closeout_body(), encoding="utf-8")
    out = read_file_result(rel, root=sandbox_root, offset=0, limit=60)
    assert out.get("selection") == "manifest"
    assert out.get("window_gated") is True
    assert "content" not in out
    assert out.get("sparse_policy")
    assert out["window_requested"] == {"offset": 0, "limit": 60}


def test_md_window_with_matching_hash_returns_lines(sandbox_root: Path) -> None:
    rel = "doc.md"
    text = "a\nb\nc\n"
    (sandbox_root / rel).write_text(text, encoding="utf-8")
    live = sha256_hex_of_file(sandbox_root / rel)
    manifest = read_file_result(rel, root=sandbox_root)
    assert manifest.get("selection") == "manifest"
    assert manifest["read_sha256"] == live
    window = read_file_result(
        rel,
        root=sandbox_root,
        offset=1,
        limit=1,
        expected_read_sha256=live,
    )
    assert window.get("selection") == "window"
    assert window.get("content", "").strip() == "b"
    assert window.get("read_sha256") == live


def test_md_window_with_wrong_hash_is_stale(sandbox_root: Path) -> None:
    rel = "stale.md"
    (sandbox_root / rel).write_text("# T\n\nbody\n", encoding="utf-8")
    out = read_file_result(
        rel,
        root=sandbox_root,
        offset=0,
        limit=5,
        expected_read_sha256="0" * 64,
    )
    assert out.get("outcome") == "stale_handle"
    assert out.get("selection") == "window"
    assert "content" not in out


def test_py_window_unchanged_without_hash(sandbox_root: Path) -> None:
    (sandbox_root / "x.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    out = read_file_result("x.py", root=sandbox_root, offset=0, limit=2)
    assert "content" in out
    assert out.get("selection") != "manifest"


def test_preamble_section_covers_executive_envelope(sandbox_root: Path) -> None:
    rel = "tmp/reviews/closeouts/sample.md"
    (sandbox_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (sandbox_root / rel).write_text(_closeout_body(), encoding="utf-8")
    pre = read_file_result(rel, root=sandbox_root, section="__preamble__")
    assert pre.get("outcome") == "ok"
    assert "status: complete" in pre.get("content", "")
    assert "effects_manifest" not in pre.get("content", "")


def test_fs_read_schema_exposes_sparse_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools._fs_read.record", lambda *a, **k: None)
    monkeypatch.setattr("tools._fs_read.toolprogress_begin", lambda *a, **k: (None, None))
    monkeypatch.setattr("tools._fs_read.toolprogress_end", lambda *a, **k: None)
    bind_fs_read_overflow({})
    mcp = FastMCP("schema-fs-read")
    register_fs_read_tool(mcp, surface="code")
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "fs_read")
    params = tool.parameters
    props = params["properties"] if isinstance(params, dict) else params.properties
    for key in ("sections", "export", "expected_read_sha256", "chunk_cursor"):
        assert key in props, f"missing fs_read param {key!r}"
    desc = tool.description or ""
    assert "manifest-first" in desc.lower() or "manifest" in desc.lower()
    assert "expected_read_sha256" in desc


def test_fs_read_forwards_chunk_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("tools._fs_read.record", lambda *a, **k: None)
    monkeypatch.setattr("tools._fs_read.toolprogress_begin", lambda *a, **k: (None, None))
    monkeypatch.setattr("tools._fs_read.toolprogress_end", lambda *a, **k: None)
    bind_fs_read_overflow({})
    mcp = FastMCP("fwd-fs-read")
    register_fs_read_tool(mcp, surface="code")
    tool = next(t for t in asyncio.run(mcp.list_tools()) if t.name == "fs_read")
    seen: dict = {}

    def _fake_impl(**kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("tools._fs_read.fs_impl", _fake_impl)
    tool.fn(
        op="read",
        sandbox="cortex",
        path="notes/x.md",
        chunk_cursor=0,
        expected_read_sha256={"notes/x.md": "a" * 64},
    )
    assert seen.get("chunk_cursor") == 0
    assert isinstance(seen.get("expected_read_sha256"), dict)
