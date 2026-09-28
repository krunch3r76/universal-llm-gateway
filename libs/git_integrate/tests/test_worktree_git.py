"""Hermetic tests for host-pinned worktree gitdir translation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.recent_commits import log_oneline
from git_integrate.worktree_git import rev_parse_head, translate_gitdir


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _linked_worktree_with_host_gitdir(
    tmp_path: Path,
    *,
    host_prefix: Path,
    container_root: Path,
) -> tuple[Path, Path]:
    """Main repo under container mount; worktree ``.git`` file pins a host gitdir."""
    main = container_root / "universal-llm-gateway"
    main.mkdir(parents=True)
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "T")
    (main / "README.md").write_text("main\n")
    _git(main, "add", "README.md")
    _git(main, "commit", "-m", "main")

    lane = container_root / "ulg-arc-worktrees" / "universal-llm-gateway" / "lane-99"
    _git(main, "worktree", "add", "-b", "cursor-sdk/lane-99", str(lane))
    (lane / "lane.txt").write_text("lane\n")
    _git(lane, "add", "lane.txt")
    _git(lane, "commit", "-m", "lane work")

    wt_admin = main / ".git" / "worktrees" / "lane-99"
    assert wt_admin.is_dir()
    host_main = host_prefix / "universal-llm-gateway"
    fake_gitdir = host_main / ".git" / "worktrees" / "lane-99"
    (lane / ".git").write_text(f"gitdir: {fake_gitdir}\n")
    return main, lane


def test_translate_gitdir_suffix_without_host_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_root = tmp_path / "container" / "projects"
    host_prefix = tmp_path / "host" / "projects"
    _main, lane = _linked_worktree_with_host_gitdir(
        tmp_path,
        host_prefix=host_prefix,
        container_root=container_root,
    )
    monkeypatch.setenv("PROJECT_ROOT", str(container_root))
    monkeypatch.delenv("GIT_INTEGRATION_SOURCE_REPO", raising=False)

    pointer = Path(f"{host_prefix}/universal-llm-gateway/.git/worktrees/lane-99")
    translated = translate_gitdir(pointer)
    assert translated.is_dir()
    assert translated.name == "lane-99"

    sha, err = rev_parse_head(lane)
    assert err is None
    assert sha and len(sha) == 40


def test_translate_gitdir_host_prefix_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_root = tmp_path / "container" / "projects"
    host_prefix = tmp_path / "host" / "projects"
    _main, lane = _linked_worktree_with_host_gitdir(
        tmp_path,
        host_prefix=host_prefix,
        container_root=container_root,
    )
    monkeypatch.setenv("PROJECT_ROOT", str(container_root))
    monkeypatch.setenv(
        "GIT_INTEGRATION_SOURCE_REPO",
        str(host_prefix / "universal-llm-gateway"),
    )

    pointer = Path(
        f"{host_prefix}/universal-llm-gateway/.git/worktrees/lane-99"
    )
    translated = translate_gitdir(pointer)
    assert translated.is_dir()

    result = log_oneline(lane, n=5)
    assert result.get("git_read_status") != "failed"
    assert result["head"]
    assert result["commits"] is not None
    assert result["commits"][0]["subject"] == "lane work"


def test_log_oneline_failure_shape_is_not_empty_success(tmp_path: Path) -> None:
    """AC2 — unreachable git must not look like zero commits since *since*."""
    dead = tmp_path / "not-a-repo"
    dead.mkdir()
    (dead / ".git").write_text("gitdir: /nonexistent/.git/worktrees/none\n")

    result = log_oneline(dead, since="091e5857ee971cebd5bcbf8edf590f5a41688dc2")
    assert result["git_read_status"] == "failed"
    assert result["git_read_reason"]
    assert result["head"] is None
    assert result["commits"] is None
