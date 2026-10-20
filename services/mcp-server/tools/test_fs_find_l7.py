"""L7 — fs find/search worktree path exclusion and search limits."""

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


_SEARCH_TOKEN = "XUNIQUE_SEARCH_TOKEN"


def _seed_search_tree(root: Path) -> None:
    main = root / "universal-llm-gateway" / "libs"
    main.mkdir(parents=True)
    (main / "hit.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")
    wt = root / "universal-llm-gateway" / ".worktrees" / "lane-1" / "libs"
    wt.mkdir(parents=True)
    (wt / "hit.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")
    arc = root / "ulg-arc-worktrees" / "repo" / "lane-2"
    arc.mkdir(parents=True)
    (arc / "hit.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")


@pytest.fixture
def search_registry(
    monkeypatch: pytest.MonkeyPatch, project_root: Path
) -> dict[str, object]:
    captured: dict[str, object] = {}

    class Capture:
        def tool(self, **_kwargs: object) -> object:
            def deco(fn: object) -> object:
                captured[fn.__name__] = fn  # type: ignore[union-attr]
                return fn

            return deco

    monkeypatch.setattr(project_mod, "record", lambda *_a, **_k: None)
    project_mod.register_project_tools(Capture())
    return {"search_project_files": captured["search_project_files"]}


def _dispatch_search(
    search_registry: dict[str, object],
    *,
    path: str = "universal-llm-gateway",
    content: str = _SEARCH_TOKEN,
    limit: int = 0,
    include_worktrees: bool = False,
) -> dict:
    return dispatch_workspaces_op(
        op="search",
        path=path,
        paths=None,
        content=content,
        target="",
        line=0,
        all_occurrences=False,
        include_untracked=True,
        binary=False,
        max_depth=3,
        offset=0,
        limit=limit,
        overflow_registry=search_registry,
        workflow_hints={},
        include_worktrees=include_worktrees,
    )


def _match_files(result: dict) -> set[str]:
    return {m["file"] for m in result.get("matches", []) if isinstance(m, dict)}


def test_search_excludes_worktrees_by_default(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    _seed_search_tree(project_root)
    result = _dispatch_search(search_registry)
    assert "error" not in result
    assert _match_files(result) == {"universal-llm-gateway/libs/hit.txt"}


def test_search_include_worktrees_returns_all(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    _seed_search_tree(project_root)
    result = _dispatch_search(search_registry, include_worktrees=True)
    assert "error" not in result
    files = _match_files(result)
    assert len(files) == 2
    assert "universal-llm-gateway/libs/hit.txt" in files
    assert any(".worktrees" in f for f in files)


def test_search_inside_worktree_path_returns_matches(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    _seed_search_tree(project_root)
    result = _dispatch_search(
        search_registry, path="universal-llm-gateway/.worktrees/lane-1"
    )
    assert "error" not in result
    assert _match_files(result) == {
        "universal-llm-gateway/.worktrees/lane-1/libs/hit.txt"
    }


def test_search_inside_ulg_arc_worktrees_returns_matches(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    _seed_search_tree(project_root)
    result = _dispatch_search(
        search_registry, path="ulg-arc-worktrees/repo/lane-2"
    )
    assert "error" not in result
    assert _match_files(result) == {"ulg-arc-worktrees/repo/lane-2/hit.txt"}


def test_search_limit_caps_match_count(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    base = project_root / "universal-llm-gateway" / "libs"
    base.mkdir(parents=True, exist_ok=True)
    for idx in range(30):
        (base / f"cap{idx:02d}.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")
    result = _dispatch_search(search_registry, limit=25)
    assert "error" not in result
    assert result["match_count"] == 25
    assert result["truncated"] is True
    assert len(result["matches"]) == 25


def test_search_limit_zero_uses_default_cap(
    project_root: Path, search_registry: dict[str, object]
) -> None:
    base = project_root / "universal-llm-gateway" / "libs"
    base.mkdir(parents=True, exist_ok=True)
    (base / "a.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")
    (base / "b.txt").write_text(_SEARCH_TOKEN, encoding="utf-8")
    result = _dispatch_search(search_registry, limit=0)
    assert "error" not in result
    assert result["match_count"] == 2
    assert result["truncated"] is False
