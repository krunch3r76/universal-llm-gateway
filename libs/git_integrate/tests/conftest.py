"""Pytest fixtures for libs/git_integrate/tests.

Fixture topology:
  source_repo  — bare-equivalent main repo with master on a parked branch
                 (master NOT checked out, enabling update-ref CAS)
  arc_worktree — linked worktree on branch arc/<arc> with a test commit
  event_log    — captured git_integrate event signals for assertion

The source_repo HEAD is parked on '_integration_parked' to avoid Git ≥2.35
worktree HEAD protection, which refuses update-ref on a branch that is HEAD
of any linked worktree. In production, the worker enforces this constraint.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """Main repo with master on a stable commit; HEAD parked off master."""
    repo = tmp_path / "source"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("base\n")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    # Park HEAD off master so update-ref CAS is not blocked by worktree protection
    _git("checkout", "-b", "_integration_parked", cwd=repo)
    return repo


@pytest.fixture
def arc_worktree(tmp_path: Path, source_repo: Path) -> Path:
    """Linked worktree on arc/test-arc with one commit ahead of master."""
    wt = tmp_path / "worktrees" / "test-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/test-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / "feature.py").write_text("# feature\n")
    _git("add", "feature.py", cwd=wt)
    _git("commit", "-m", "add feature", cwd=wt)
    return wt


@pytest.fixture
def event_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture git_integrate event signals."""
    log: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        log.append((signal, payload))

    monkeypatch.setattr("git_integrate.events.record", _record)
    return log
