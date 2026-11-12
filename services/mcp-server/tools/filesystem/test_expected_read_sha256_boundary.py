"""S0 a:43743 — coerce expected_read_sha256 maps before markdown dispatch."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastmcp import FastMCP
from fs_impl import fs_impl

from tools._file_helpers import read_file_result
from tools._hashing import sha256_hex_of_file
from tools.filesystem._expected_read_sha256_boundary import (
    coerce_expected_read_sha256_at_fs_boundary,
    coerce_expected_read_sha256_for_md,
)
from tools.filesystem._files_dispatcher import dispatch_files_op
from tools.filesystem._ops_text import read_files_batch_impl
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


def _patch_cortex_read(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr("tools.filesystem._paths.SANDBOX_ROOT", root)
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_file_result",
        lambda path, **kwargs: read_file_result(path, root=root, **kwargs),
    )


def _patch_ws_read(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_file_result",
        lambda path, **kwargs: read_file_result(path, root=root, **kwargs),
    )
    monkeypatch.setattr(
        "tools.filesystem._ops_text.read_files_batch",
        lambda paths, **kwargs: read_files_batch_impl(paths, **kwargs),
    )


def _fs_common(**extra):
    base = {
        "surface": "code",
        "overflow_registry": {},
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
    assert out.get("code") == "invalid_parameter"
    assert out.get("retryable") is True
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


def test_markdown_tool_to_dict_dict_never_attribute_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct markdown MCP path must not crash on map-shaped precondition."""
    root = tmp_path / "files"
    root.mkdir()
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    md_fn = _md_registry(root, monkeypatch)
    out = md_fn(
        op="to_dict",
        path=rel,
        sandbox="cortex",
        expected_read_sha256={rel: live},
    )
    assert out.get("code") == "param_not_applicable"
    assert "AttributeError" not in str(out)


def test_markdown_tool_read_section_matching_dict_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "files"
    root.mkdir()
    rel = "notes/doc.md"
    target = root / rel
    target.parent.mkdir(parents=True)
    target.write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(target)
    md_fn = _md_registry(root, monkeypatch)
    out = md_fn(
        op="read_section",
        path=rel,
        sandbox="cortex",
        section="Title",
        expected_read_sha256={rel: live},
    )
    assert "error" not in out, out
    assert out.get("code") != "param_not_applicable", out
    assert "body" in out.get("content", "")


def test_cortex_read_matching_map_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(root / rel)
    _patch_cortex_read(monkeypatch, root)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: root)
    out = fs_impl(
        **_fs_common(
            overflow_registry={"files": dispatch_files_op},
            op="read",
            sandbox="cortex",
            path=rel,
            section="Title",
            expected_read_sha256={rel: live},
        )
    )
    assert out.get("code") != "invalid_parameter", out
    assert "AttributeError" not in str(out)
    assert "body" in out.get("content", "")


def test_cortex_read_missing_key_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    _patch_cortex_read(monkeypatch, root)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: root)
    out = fs_impl(
        **_fs_common(
            overflow_registry={"files": dispatch_files_op},
            op="read",
            sandbox="cortex",
            path=rel,
            expected_read_sha256={"other.md": "a" * 64},
        )
    )
    assert out.get("code") == "invalid_parameter"
    assert out.get("retryable") is True


def test_workspaces_read_matching_map_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "projects"
    rel = "universal-llm-gateway/notes/doc.md"
    (projects / rel).parent.mkdir(parents=True)
    (projects / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(projects / rel)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: projects)
    monkeypatch.setattr("tools.project._PROJECT_ROOT", projects)
    _patch_ws_read(monkeypatch, projects)
    out = fs_impl(
        **_fs_common(
            op="read",
            sandbox="workspaces",
            path=rel,
            section="Title",
            expected_read_sha256={
                "workspaces://universal-llm-gateway/notes/doc.md": live,
            },
        )
    )
    assert out.get("code") != "invalid_parameter", out
    assert "body" in out.get("content", "")


def test_cortex_read_workspaces_uri_key_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(root / rel)
    _patch_cortex_read(monkeypatch, root)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: root)
    out = fs_impl(
        **_fs_common(
            overflow_registry={"files": dispatch_files_op},
            op="read",
            sandbox="cortex",
            path=rel,
            expected_read_sha256={
                f"workspaces://universal-llm-gateway/{rel}": live,
            },
        )
    )
    assert out.get("code") == "invalid_parameter"
    assert out.get("retryable") is True


def test_ambiguous_map_foreign_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(root / rel)
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(
            md_fn,
            op="md_read",
            expected_read_sha256={rel: live, "foreign.md": live},
        )
    )
    assert out.get("code") == "invalid_parameter"
    assert "foreign.md" in out["data"]["keys"]


def test_ambiguous_conflicting_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    live = sha256_hex_of_file(root / rel)
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(
            md_fn,
            op="md_read",
            expected_read_sha256={rel: live, f"cortex://{rel}": "0" * 64},
        )
    )
    assert out.get("code") == "invalid_parameter"


def test_empty_map_md_read_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    _patch_cortex_read(monkeypatch, root)
    monkeypatch.setattr("fs_roots.fs_root_for", lambda _s, rid: root)
    md_fn = _md_registry(root, monkeypatch)
    md_out = fs_impl(**_fs_kwargs(md_fn, op="md_read", expected_read_sha256={}))
    read_out = fs_impl(
        **_fs_common(
            overflow_registry={"files": dispatch_files_op},
            op="read",
            sandbox="cortex",
            path=rel,
            expected_read_sha256={},
        )
    )
    assert md_out.get("code") == "invalid_parameter"
    assert read_out.get("code") == "invalid_parameter"


def test_map_value_whitespace_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "files"
    rel = "notes/doc.md"
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text("# Title\n\nbody\n", encoding="utf-8")
    md_fn = _md_registry(root, monkeypatch)
    out = fs_impl(
        **_fs_kwargs(md_fn, op="md_read", expected_read_sha256={rel: "   "}),
    )
    assert out.get("code") == "invalid_parameter"
    assert rel in out["data"]["keys"]


def test_bare_key_after_lane_style_path() -> None:
    digest, err = coerce_expected_read_sha256_at_fs_boundary(
        op="read",
        expected={"notes/doc.md": "a" * 64},
        path="universal-llm-gateway/notes/doc.md",
        sandbox="workspaces",
    )
    assert err is None
    assert digest == "a" * 64
