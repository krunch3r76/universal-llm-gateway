"""GIW git hook install (common-dir symlinks, preserves other hooks)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.lane_b_git_hook_install import (
    GitHookInstallResult,
    ensure_git_hooks_installed,
    giw_git_hooks_dir,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    base = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        base.pop(key, None)
    if env:
        base.update(env)
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=base,
    )


def test_ensure_git_hooks_symlinks_without_hooks_path_and_preserves_pre_commit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")

    hooks_raw = _git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
    common_hooks = Path(hooks_raw)
    if not common_hooks.is_absolute():
        common_hooks = (repo / common_hooks).resolve()
    else:
        common_hooks = common_hooks.resolve()
    common_hooks.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "pre-commit-ran"
    pre_commit = common_hooks / "pre-commit"
    pre_commit.write_text(
        f"#!/usr/bin/env bash\n: > {marker}\n",
        encoding="utf-8",
    )
    pre_commit.chmod(0o755)

    result = ensure_git_hooks_installed(repo)
    assert result.status == "installed"
    assert result.common_hooks_dir == common_hooks
    giw = giw_git_hooks_dir()
    for name in ("reference-transaction", "pre-push"):
        link = common_hooks / name
        assert link.is_symlink()
        assert link.resolve() == (giw / name).resolve()

    hooks_path = _git(repo, "config", "--get", "core.hooksPath")
    assert hooks_path.returncode != 0

    (repo / "second.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "second.txt")
    commit = _git(repo, "commit", "-m", "second")
    assert commit.returncode == 0
    assert marker.is_file()

    _commit_third = (repo / "third.txt").write_text("y\n", encoding="utf-8")
    _ = _commit_third
    _git(repo, "add", "third.txt")
    _git(repo, "commit", "-m", "third")
    env = os.environ.copy()
    env.pop("CURSOR_SDK_DISPATCH_ID", None)
    reset = _git(repo, "reset", "--hard", "HEAD~1", env=env)
    assert reset.returncode != 0


def test_ensure_git_hooks_skipped_when_hooks_path_set(tmp_path: Path) -> None:
    repo = tmp_path / "r2"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    _git(repo, "config", "core.hooksPath", "/tmp/other-hooks")
    result = ensure_git_hooks_installed(repo)
    assert result.status == "skipped_hooks_path"
    assert result.detail == "/tmp/other-hooks"


def test_ensure_git_hooks_refused_when_hook_path_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "r3"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    hooks_raw = _git(repo, "rev-parse", "--git-path", "hooks").stdout.strip()
    common_hooks = Path(hooks_raw)
    if not common_hooks.is_absolute():
        common_hooks = (repo / common_hooks).resolve()
    else:
        common_hooks = common_hooks.resolve()
    common_hooks.mkdir(parents=True, exist_ok=True)
    (common_hooks / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    result = ensure_git_hooks_installed(repo)
    assert isinstance(result, GitHookInstallResult)
    assert result.status == "refused"
    assert "pre-push" in result.detail
