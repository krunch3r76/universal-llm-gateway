"""Tests for ``snapshot_op`` — capture fidelity, reset semantics, reject paths."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grokbuild import snapshot_op


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    r = tmp_path / "src"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("base\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    import grokbuild.worktree as wt

    monkeypatch.setattr(wt, "WORKTREE_ROOT", str(tmp_path / "worktrees"))
    monkeypatch.setattr(wt, "ALLOWED_SOURCE_ROOT", str(tmp_path))
    return r


@pytest.mark.asyncio
async def test_captures_tracked_untracked_and_deletions(repo: Path):
    (repo / "a.txt").write_text("modified\n")
    (repo / "new.txt").write_text("untracked\n")
    (repo / "del.txt").write_text("x\n")
    _git(repo, "add", "del.txt")
    _git(repo, "commit", "-qm", "add-del")
    (repo / "del.txt").unlink()
    env = await snapshot_op(source_repo=str(repo), slug="t-1")
    assert env["status"] == "completed"
    wt_path = Path(env["metadata"]["worktree_path"])
    assert (wt_path / "a.txt").read_text() == "modified\n"
    assert (wt_path / "new.txt").read_text() == "untracked\n"
    assert not (wt_path / "del.txt").exists()
    assert (repo / "new.txt").exists()


@pytest.mark.asyncio
async def test_clean_tree_rejected(repo: Path):
    env = await snapshot_op(source_repo=str(repo), slug="t-2")
    assert env["status"] == "rejected"
    assert env["metadata"]["reason_code"] == "clean_tree"


@pytest.mark.asyncio
async def test_reset_main_clears_tree_after_snapshot(repo: Path):
    (repo / "a.txt").write_text("dirty\n")
    (repo / "u.txt").write_text("u\n")
    env = await snapshot_op(source_repo=str(repo), slug="t-3", reset_main=True)
    assert env["status"] == "completed"
    assert env["metadata"]["main_reset"] == "ok"
    assert (repo / "a.txt").read_text() == "base\n"
    assert not (repo / "u.txt").exists()
    wt_path = Path(env["metadata"]["worktree_path"])
    assert (wt_path / "a.txt").read_text() == "dirty\n"


@pytest.mark.asyncio
async def test_real_index_untouched(repo: Path):
    before = _git(repo, "status", "--porcelain")
    (repo / "a.txt").write_text("x\n")
    await snapshot_op(source_repo=str(repo), slug="t-4")
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    _ = before
