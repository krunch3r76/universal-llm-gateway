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
    """Minimal doc for window-gate tests (not a real sidecar shape)."""
    lines = ["status: complete", "line two", ""]
    lines.append("## effects_manifest")
    lines.extend(f"effect {i}" for i in range(5))
    return "\n".join(lines)


def _realistic_closeout_sidecar() -> str:
    return "\n".join(
        [
            "## executive_envelope",
            "",
            "status: complete",
            "commit: deadbeef",
            "",
            "### effects_manifest",
            "",
            "short summary only",
            "",
            "## effects_manifest",
            "",
            "TOP_LEVEL_EFFECTS_BLOB " + ("x" * 200),
            "",
            "## usage",
            "",
            "usage block",
            "",
            "## structured_closeout_full",
            "",
            "full dump",
        ]
    )


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


def test_closeout_sections_recipe_returns_executive_envelope(
    sandbox_root: Path,
) -> None:
    rel = "tmp/reviews/closeouts/sample.md"
    (sandbox_root / rel).parent.mkdir(parents=True, exist_ok=True)
    (sandbox_root / rel).write_text(_realistic_closeout_sidecar(), encoding="utf-8")
    manifest = read_file_result(rel, root=sandbox_root)
    assert manifest.get("selection") == "manifest"
    assert "__preamble__" not in {row.get("id") for row in manifest.get("sections", [])}
    ids = {row.get("id") for row in manifest.get("sections", [])}
    assert "executive_envelope" in ids
    batch = read_file_result(
        rel,
        root=sandbox_root,
        sections=["executive_envelope", "executive_envelope/effects_manifest"],
    )
    assert batch.get("selection") == "sections"
    bodies = "".join(item.get("content", "") for item in batch.get("items", []))
    assert "status: complete" in bodies
    assert "short summary only" in bodies
    assert "TOP_LEVEL_EFFECTS_BLOB" not in bodies
    assert "structured_closeout_full" not in bodies
    top_level = read_file_result(rel, root=sandbox_root, section="effects_manifest")
    assert "TOP_LEVEL_EFFECTS_BLOB" in top_level.get("content", "")


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
