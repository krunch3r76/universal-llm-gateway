"""GIW git hook install (core.hooksPath)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.lane_b_git_hook_install import (
    ensure_git_hooks_installed,
    giw_git_hooks_dir,
)

pytestmark = pytest.mark.offline


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_ensure_git_hooks_installed_sets_hooks_path(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "master")
    hooks = ensure_git_hooks_installed(repo)
    assert hooks == giw_git_hooks_dir()
    got = _git(repo, "config", "--get", "core.hooksPath")
    assert got.returncode == 0
    assert got.stdout.strip() == str(hooks)
