"""fs(thread=…) + recent_commits against host-pinned lane worktree gitdirs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fs_impl import fs_impl
from project_ops import workspaces_impl_registry


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _lane_fixture(
    tmp_path: Path,
    *,
    thread_id: str = "13130",
) -> tuple[Path, Path]:
    container_root = tmp_path / "projects"
    host_prefix = tmp_path / "mnt" / "torus" / "projects"
    main = container_root / "universal-llm-gateway"
    main.mkdir(parents=True)
    (main / "scripts").mkdir(parents=True)
    (main / "scripts" / "check-imports").write_text("")
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "T")
    (main / "README.md").write_text("x\n")
    _git(main, "add", "README.md")
    _git(main, "commit", "-m", "init")

    dirname = f"lane-{thread_id}"
    lane = container_root / "ulg-arc-worktrees" / "universal-llm-gateway" / dirname
    branch = f"cursor-sdk/lane-{thread_id}"
    _git(main, "worktree", "add", "-b", branch, str(lane))
    (lane / "w.txt").write_text("w\n")
    _git(lane, "add", "w.txt")
    _git(lane, "commit", "-m", "lane tip")

    fake_gitdir = (
        host_prefix / "universal-llm-gateway" / ".git" / "worktrees" / dirname
    )
    (lane / ".git").write_text(f"gitdir: {fake_gitdir}\n")
    return container_root, main, lane


def test_fs_recent_commits_thread_returns_head_and_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = "13130"
    container_root, main, lane = _lane_fixture(tmp_path, thread_id=thread_id)
    since = subprocess.run(
        ["git", "-C", str(main), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    monkeypatch.setenv("PROJECT_ROOT", str(container_root))
    monkeypatch.setattr("lane_branch_root.project_root_path", lambda: container_root)
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _b: f"lane-{thread_id}",
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": thread_id,
            "current_branch": f"cursor-sdk/lane-{thread_id}",
            "association_id": 1,
            "state": "associated",
        },
    ):
        result = fs_impl(
            surface="life",
            overflow_registry=workspaces_impl_registry(),
            op="recent_commits",
            sandbox="workspaces",
            path="universal-llm-gateway",
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
            thread=thread_id,
            since=since,
        )

    assert "error" not in result, result
    assert result.get("git_read_status") != "failed", result
    assert result["head"]
    assert isinstance(result["commits"], list)
    assert len(result["commits"]) >= 1
    assert result["lane_thread"] == thread_id
    assert result["lane_worktree_root"] == str(lane.resolve())
    assert "workspaces_read_at_head" in result


def test_fs_recent_commits_thread_git_failure_not_quiet_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_id = "13130"
    container_root, _main, lane = _lane_fixture(tmp_path, thread_id=thread_id)
    (lane / ".git").write_text("gitdir: /totally/missing/.git/worktrees/none\n")

    monkeypatch.setenv("PROJECT_ROOT", str(container_root))
    monkeypatch.setattr("lane_branch_root.project_root_path", lambda: container_root)
    monkeypatch.setattr(
        "lane_branch_root.worktree_dirname_for_branch",
        lambda _b: f"lane-{thread_id}",
    )
    with patch(
        "lane_branch_root.relay",
        return_value={
            "thread_id": thread_id,
            "current_branch": f"cursor-sdk/lane-{thread_id}",
            "association_id": 1,
            "state": "associated",
        },
    ):
        result = fs_impl(
            surface="life",
            overflow_registry=workspaces_impl_registry(),
            op="recent_commits",
            sandbox="workspaces",
            path="universal-llm-gateway",
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
            thread=thread_id,
            since="091e5857ee971cebd5bcbf8edf590f5a41688dc2",
        )

    assert result.get("git_read_status") == "failed"
    assert result.get("git_read_reason")
    assert result.get("head") is None
    assert result.get("commits") is None
    assert "workspaces_head_unknown" in result
