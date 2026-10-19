"""L7 — fs find excludes worktree paths by default."""

from __future__ import annotations

from pathlib import Path

import pytest
from fs_impl import fs_impl

from tools import project as project_mod
from tools.filesystem._fs_dispatch import dispatch_workspaces_op


@pytest.fixture
def project_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / ".git").mkdir()
    monkeypatch.setenv("PROJECT_ROOT", str(root))
    monkeypatch.setattr(project_mod, "_PROJECT_ROOT", root)
    return root


def _seed_find_tree(root: Path) -> None:
    main = root / "universal-llm-gateway" / "libs"
    main.mkdir(parents=True)
    (main / "needle.txt").write_text("main", encoding="utf-8")
    wt = root / "universal-llm-gateway" / ".worktrees" / "lane-1" / "libs"
    wt.mkdir(parents=True)
    (wt / "needle.txt").write_text("worktree", encoding="utf-8")
    arc = root / "ulg-arc-worktrees" / "repo" / "lane-2"
    arc.mkdir(parents=True)
    (arc / "needle.txt").write_text("arc", encoding="utf-8")


def test_find_excludes_worktrees_by_default(project_root: Path) -> None:
    _seed_find_tree(project_root)
    result = dispatch_workspaces_op(
        op="find",
        path="universal-llm-gateway",
        paths=None,
        content="needle.txt",
        target="",
        line=0,
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=0,
        overflow_registry={},
        workflow_hints={},
    )
    assert "error" not in result
    assert result["matches"] == ["universal-llm-gateway/libs/needle.txt"]


def test_find_include_worktrees_returns_all(project_root: Path) -> None:
    _seed_find_tree(project_root)
    result = dispatch_workspaces_op(
        op="find",
        path="universal-llm-gateway",
        paths=None,
        content="needle.txt",
        target="",
        line=0,
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=0,
        overflow_registry={},
        workflow_hints={},
        include_worktrees=True,
    )
    assert "error" not in result
    assert len(result["matches"]) == 2
    assert "universal-llm-gateway/libs/needle.txt" in result["matches"]
    assert any(".worktrees" in m for m in result["matches"])


def _fs_find_impl(
    project_root: Path,
    *,
    path: str,
    content: str,
    include_worktrees: bool = False,
) -> dict:
    return fs_impl(
        surface="code",
        overflow_registry={},
        op="find",
        sandbox="workspaces",
        path=path,
        paths=None,
        content=content,
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
        include_worktrees=include_worktrees,
    )


def test_fs_impl_find_excludes_worktrees_by_default(project_root: Path) -> None:
    _seed_find_tree(project_root)
    result = _fs_find_impl(
        project_root, path="universal-llm-gateway", content="needle.txt"
    )
    assert "error" not in result
    assert result["matches"] == ["universal-llm-gateway/libs/needle.txt"]


def test_fs_impl_find_include_worktrees(project_root: Path) -> None:
    _seed_find_tree(project_root)
    result = _fs_find_impl(
        project_root,
        path="universal-llm-gateway",
        content="needle.txt",
        include_worktrees=True,
    )
    assert "error" not in result
    assert len(result["matches"]) == 2


def test_fs_impl_find_inside_worktree_path_returns_matches(project_root: Path) -> None:
    _seed_find_tree(project_root)
    wt_path = "universal-llm-gateway/.worktrees/lane-1"
    result = _fs_find_impl(project_root, path=wt_path, content="needle.txt")
    assert "error" not in result
    assert result["matches"] == ["universal-llm-gateway/.worktrees/lane-1/libs/needle.txt"]


def test_fs_impl_find_inside_ulg_arc_worktrees(project_root: Path) -> None:
    _seed_find_tree(project_root)
    result = _fs_find_impl(
        project_root, path="ulg-arc-worktrees/repo/lane-2", content="needle.txt"
    )
    assert "error" not in result
    assert result["matches"] == ["ulg-arc-worktrees/repo/lane-2/needle.txt"]
