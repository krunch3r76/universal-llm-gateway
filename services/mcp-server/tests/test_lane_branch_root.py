"""Tests for fs(thread=...) lane worktree root resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fs_impl import fs_impl
from lane_branch_root import (
    LaneBranchResolutionError,
    worktree_dirname_for_branch,
)

HOST_STYLE_PORCELAIN = """\
worktree /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/cursor-sdk-abc
HEAD deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
branch refs/heads/cursor-sdk/abc

worktree /mnt/torus/projects/universal-llm-gateway
HEAD cafebabecafebabecafebabecafebabecafebabe
branch refs/heads/master
"""

DISAGREEING_NAMES_PORCELAIN = """\
worktree /mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/cursor-sdk-auto-a6a6daacdfc6
HEAD deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
branch refs/heads/arc/cortex-assertion-update-legibility-land
"""


def test_worktree_dirname_for_branch_returns_basename_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lane_branch_root.project_root_path",
        lambda: Path("/data/project"),
    )

    class _Proc:
        returncode = 0
        stdout = HOST_STYLE_PORCELAIN
        stderr = ""

    monkeypatch.setattr("lane_branch_root.subprocess.run", lambda *_a, **_k: _Proc())

    dirname = worktree_dirname_for_branch("cursor-sdk/abc")

    assert dirname == "cursor-sdk-abc"


def test_worktree_dirname_for_branch_matches_branch_not_directory_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lane_branch_root.project_root_path",
        lambda: Path("/data/project"),
    )

    class _Proc:
        returncode = 0
        stdout = DISAGREEING_NAMES_PORCELAIN
        stderr = ""

    monkeypatch.setattr("lane_branch_root.subprocess.run", lambda *_a, **_k: _Proc())

    dirname = worktree_dirname_for_branch("arc/cortex-assertion-update-legibility-land")

    assert dirname == "cursor-sdk-auto-a6a6daacdfc6"


def test_branch_for_thread_state_none_raises() -> None:
    from lane_branch_root import branch_for_thread

    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "7119",
            "current_branch": None,
            "association_id": None,
            "state": "none",
        },
    ):
        with pytest.raises(LaneBranchResolutionError, match="state='none'"):
            branch_for_thread("7119")


def test_worktree_dirname_for_branch_no_match_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "lane_branch_root.project_root_path",
        lambda: Path("/data/project"),
    )

    class _Proc:
        returncode = 0
        stdout = HOST_STYLE_PORCELAIN
        stderr = ""

    monkeypatch.setattr("lane_branch_root.subprocess.run", lambda *_a, **_k: _Proc())

    with pytest.raises(LaneBranchResolutionError, match="no matching worktree"):
        worktree_dirname_for_branch("missing/branch")


def test_root_for_thread_resolves_per_repo_subroot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GIW layout: {parent}/ulg-arc-worktrees/{repo}/lane-{thread}."""
    from lane_branch_root import root_for_thread

    parent = tmp_path / "projects"
    repo = parent / "universal-llm-gateway"
    lane = parent / "ulg-arc-worktrees" / "universal-llm-gateway" / "lane-7119"
    repo.mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check-imports").write_text("")
    lane.mkdir(parents=True)
    monkeypatch.setattr("lane_branch_root.project_root_path", lambda: parent)
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _branch: "lane-7119",
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "7119",
            "current_branch": "cursor-sdk/lane-7119",
            "association_id": 1,
            "state": "associated",
        },
    ):
        assert root_for_thread("7119") == lane


def test_root_for_thread_missing_directory_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from lane_branch_root import root_for_thread

    project_root = tmp_path / "project"
    project_root.mkdir()
    repo = project_root / "universal-llm-gateway"
    repo.mkdir()
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check-imports").write_text("")
    monkeypatch.setenv("LANE_WORKTREE_ROOT_DIRNAME", "ulg-arc-worktrees")
    monkeypatch.setattr(
        "lane_branch_root.project_root_path",
        lambda: project_root,
    )
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _branch: "cursor-sdk-abc",
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "7119",
            "current_branch": "cursor-sdk/abc",
            "association_id": 1,
            "state": "associated",
        },
    ):
        with pytest.raises(LaneBranchResolutionError, match="not a directory"):
            root_for_thread("7119")


def test_fs_impl_thread_none_uses_bind_workspaces_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = MagicMock()
    bind.return_value.__enter__ = MagicMock(return_value=Path("/shared"))
    bind.return_value.__exit__ = MagicMock(return_value=False)
    relay = MagicMock()

    monkeypatch.setattr("fs_impl.bind_workspaces_root", bind)
    monkeypatch.setattr("lane_branch_root.relay", relay)

    def _dispatch(*_args, **_kwargs):
        return {"content": "ok", "path": "universal-llm-gateway/README.md"}

    monkeypatch.setattr("fs_impl.dispatch_workspaces_op", _dispatch)
    monkeypatch.setattr(
        "fs_impl.workspaces_impl_registry",
        lambda: {},
    )

    result = fs_impl(
        surface="code",
        overflow_registry={},
        op="read",
        sandbox="workspaces",
        path="universal-llm-gateway/README.md",
        paths=None,
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
        thread=None,
    )

    assert "error" not in result, result
    bind.assert_called_once_with("code")
    relay.assert_not_called()


def test_fs_impl_thread_unresolvable_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "fs_impl.workspaces_impl_registry",
        lambda: {},
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "7119",
            "current_branch": None,
            "association_id": None,
            "state": "none",
        },
    ):
        result = fs_impl(
            surface="code",
            overflow_registry={},
            op="read",
            sandbox="workspaces",
            path="universal-llm-gateway/README.md",
            paths=None,
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
            thread="7119",
        )

    assert "error" in result
    assert "7119" in result["error"]
    assert "state='none'" in result["error"]


def test_fs_impl_thread_read_resolves_under_lane_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fs(read, thread=…) must read from the lane worktree, not shared checkout."""
    from project_ops import workspaces_impl_registry

    parent = tmp_path / "projects"
    repo = parent / "universal-llm-gateway"
    lane = parent / "ulg-arc-worktrees" / "universal-llm-gateway" / "lane-11579"
    skill = lane / ".cursor" / "skills" / "liaison" / "SKILL.md"
    repo.mkdir(parents=True)
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "check-imports").write_text("")
    skill.parent.mkdir(parents=True)
    skill.write_text("lane liaison skill body\n")
    shared_skill = repo / ".cursor" / "skills" / "liaison" / "SKILL.md"
    shared_skill.parent.mkdir(parents=True)
    shared_skill.write_text("shared checkout body\n")

    monkeypatch.setattr("lane_branch_root.project_root_path", lambda: parent)
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _branch: "lane-11579",
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": "11579",
            "current_branch": "cursor-sdk/lane-11579",
            "association_id": 1,
            "state": "associated",
        },
    ):
        result = fs_impl(
            surface="code",
            overflow_registry=workspaces_impl_registry(),
            op="read",
            sandbox="workspaces",
            path=".cursor/skills/liaison/SKILL.md",
            paths=None,
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
            thread="11579",
        )

    assert "error" not in result, result
    assert result["content"] == "lane liaison skill body\n"
    assert result["lane_worktree_root"] == str(lane.resolve())
