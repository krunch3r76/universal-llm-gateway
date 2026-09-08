"""Tests for Lane-B worktree pin + git lock (S1 Leg A)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_worktree_lock import (
    ForeignLockError,
    list_locked_worktrees,
    lock_lane_worktree,
    parse_lock_reason,
    unlock_lane_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    ensure_worktree_schema,
    pin_lane_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()
    yield
    CursorDispatchLedger._instance = None


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x\n")
    _git("add", "f", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


def _add_worktree(repo: Path, wt: Path, branch: str = "lane-b") -> None:
    tip = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    _git("branch", branch, tip, cwd=repo)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", str(wt), branch, cwd=repo)


def test_parse_lock_reason_accepts_ulg_grammar() -> None:
    reason = "ulg:dispatch=d1;thread=t1;pinned_at=2026-01-01T00:00:00Z"
    parsed = parse_lock_reason(reason)
    assert parsed is not None
    assert parsed.dispatch_id == "d1"
    assert parsed.thread_id == "t1"


def test_parse_lock_reason_rejects_foreign() -> None:
    assert parse_lock_reason("manual lock") is None


def test_lock_refuses_git_worktree_remove(git_repo: Path, tmp_path: Path) -> None:
    """AC-S1-2: pinned lane survives ``git worktree remove --force``."""
    wt = tmp_path / "lane"
    _add_worktree(git_repo, wt)
    lock_lane_worktree(
        git_repo,
        wt,
        dispatch_id="disp-1",
        thread_id="thread-1",
    )
    proc = subprocess.run(
        ["git", "-C", str(git_repo), "worktree", "remove", "--force", str(wt)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 128
    assert wt.is_dir()


def test_lock_idempotent_same_thread(git_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "lane"
    _add_worktree(git_repo, wt)
    first = lock_lane_worktree(
        git_repo, wt, dispatch_id="d1", thread_id="t1"
    )
    second = lock_lane_worktree(
        git_repo, wt, dispatch_id="d2", thread_id="t1"
    )
    assert first != second
    locked = list_locked_worktrees(git_repo)
    assert len(locked) == 1
    assert locked[0].parsed is not None
    assert locked[0].parsed.dispatch_id == "d2"


def test_foreign_lock_raises(git_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "lane"
    _add_worktree(git_repo, wt)
    subprocess.run(
        [
            "git",
            "-C",
            str(git_repo),
            "worktree",
            "lock",
            "--reason",
            "foreign",
            str(wt),
        ],
        check=True,
    )
    with pytest.raises(ForeignLockError):
        lock_lane_worktree(git_repo, wt, dispatch_id="d1", thread_id="t1")


def test_pin_lane_worktree_supersedes_active(git_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "lane"
    _add_worktree(git_repo, wt)
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        pin_lane_worktree(
            conn,
            source_repo=git_repo,
            thread_id="t1",
            dispatch_id="d1",
            worktree_path=wt,
            lock_reason="ulg:dispatch=d1;thread=t1;pinned_at=2026-01-01T00:00:00Z",
        )
        pin_lane_worktree(
            conn,
            source_repo=git_repo,
            thread_id="t1",
            dispatch_id="d2",
            worktree_path=wt,
            lock_reason="ulg:dispatch=d2;thread=t1;pinned_at=2026-01-02T00:00:00Z",
        )
        row = conn.execute(
            "SELECT COUNT(*) FROM cursor_sdk_lane_worktree_pins "
            "WHERE source_repo=? AND thread_id=? AND released_at IS NOT NULL",
            (str(git_repo.resolve()), "t1"),
        ).fetchone()
    assert row is not None and int(row[0]) == 1


def test_unlock_lane_worktree(git_repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "lane"
    _add_worktree(git_repo, wt)
    lock_lane_worktree(git_repo, wt, dispatch_id="d1", thread_id="t1")
    assert unlock_lane_worktree(git_repo, wt, thread_id="t1")
    assert not list_locked_worktrees(git_repo)
