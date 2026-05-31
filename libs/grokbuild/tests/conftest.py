"""Pytest configuration for libs/grokbuild/tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def worktree_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect WORKTREE_ROOT and ALLOWED_SOURCE_ROOT under tmp_path.

    Worktree handlers compute paths from the module constants; monkeypatching
    them lets tests exercise the real ``git worktree add`` flow against
    pytest-managed tmp dirs.
    """
    root = tmp_path / "worktrees"
    allowed = str(tmp_path)
    monkeypatch.setattr("grokbuild.worktree.WORKTREE_ROOT", str(root))
    monkeypatch.setattr("grokbuild.worktree.ALLOWED_SOURCE_ROOT", allowed)
    return root
