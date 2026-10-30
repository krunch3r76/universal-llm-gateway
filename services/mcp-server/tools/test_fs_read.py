"""Tests for read-only fs_read companion tool."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._fs_read import (  # noqa: E402
    FS_READ_OPS,
    FS_READ_REJECTED_WRITE_OPS,
    bind_fs_read_overflow,
    register_fs_read_tool,
)


@pytest.fixture
def fs_read_fn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("tools._fs_read.record", lambda *a, **k: None)
    monkeypatch.setattr("tools._fs_read.toolprogress_begin", lambda *a, **k: (None, None))
    monkeypatch.setattr("tools._fs_read.toolprogress_end", lambda *a, **k: None)
    bind_fs_read_overflow({})
    mcp = FastMCP("test-fs-read")
    register_fs_read_tool(mcp, surface="code")
    tools = asyncio.run(mcp.list_tools())
    tool = next(t for t in tools if t.name == "fs_read")
    assert tool.annotations is not None
    assert tool.annotations.readOnlyHint is True
    return tool.fn


@pytest.mark.parametrize("op", sorted(FS_READ_REJECTED_WRITE_OPS | {"unknown_op"}))
def test_fs_read_rejects_mutating_ops(fs_read_fn, op: str) -> None:
    out = fs_read_fn(op=op, sandbox="cortex", path="notes/x.md")
    assert out.get("reason") == "not_a_read_op"
    assert "fs" in out.get("error", "")


def test_fs_read_admits_whitelist_ops(fs_read_fn, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def _fake_impl(**kwargs):
        seen.append(kwargs["op"])
        return {"ok": True, "op": kwargs["op"]}

    monkeypatch.setattr("tools._fs_read.fs_impl", _fake_impl)

    def _call(op: str):
        if op == "resolve_sha256":
            return fs_read_fn(op=op, sandbox="cortex", content="deadbeef")
        if op in {"find", "recent_commits"}:
            return fs_read_fn(op=op, sandbox="workspaces", path=".")
        if op == "read_multi":
            return fs_read_fn(op=op, sandbox="cortex", paths=["notes/x.md"])
        return fs_read_fn(op=op, sandbox="cortex", path="notes/x.md", content="x")

    for op in sorted(FS_READ_OPS):
        out = _call(op)
        assert out.get("ok") is True, op
    assert seen == sorted(FS_READ_OPS)


def test_fs_read_life_workspaces_respects_read_grant(
    fs_read_fn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tools._fs_read.fs_impl", lambda **k: {"ok": True})
    out = fs_read_fn(
        op="write",
        sandbox="workspaces",
        path="universal-llm-gateway/x.py",
    )
    assert out.get("reason") == "not_a_read_op"

    monkeypatch.setattr(
        "tools._fs_read.current_request_metadata",
        lambda: {"surface": "life"},
    )
    out = fs_read_fn(
        op="resolve_sha256",
        sandbox="workspaces",
        content="abc",
    )
    assert out.get("reason") == "not_a_read_op"


def test_fs_read_thread_with_read_delegates(
    fs_read_fn, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def _fake_impl(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "read_sha256": "deadbeef"}

    monkeypatch.setattr("tools._fs_read.fs_impl", _fake_impl)
    out = fs_read_fn(
        op="read",
        sandbox="workspaces",
        path="universal-llm-gateway/README.md",
        thread="21297",
    )
    assert "error" not in out or out.get("content") == "ok"
    assert captured.get("thread") == "21297"


def test_fs_read_operator_brief_and_skill_paths(
    fs_read_fn, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_impl(**kwargs):
        return {
            "content": "# brief",
            "read_sha256": "abc123",
            "path": kwargs.get("path", ""),
        }

    monkeypatch.setattr("tools._fs_read.fs_impl", _fake_impl)
    brief = fs_read_fn(
        op="read",
        sandbox="cortex",
        path="cortex://notes/traid/orion/operator-brief-v1.md",
    )
    assert brief.get("read_sha256") == "abc123"
    skill = fs_read_fn(
        op="read",
        sandbox="workspaces",
        path="workspaces://universal-llm-gateway/cursor-plugins/ulg-ecosystem/skills/work-item-seed-path/SKILL.md",
    )
    assert skill.get("read_sha256") == "abc123"
