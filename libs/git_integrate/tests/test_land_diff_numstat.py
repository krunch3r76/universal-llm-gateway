"""Lib tests for ``land_diff_numstat`` (C6, thread 1147)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from git_integrate.git_cas import land_diff_numstat, land_diff_text, land_fingerprint


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _merge_base(worktree: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "HEAD", "refs/heads/master"],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_land_diff_numstat_clean_matches_git(arc_worktree: Path) -> None:
    wt = str(arc_worktree)
    mb = _merge_base(arc_worktree)
    expected = subprocess.run(
        ["git", "-C", wt, "diff", "--numstat", mb, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert land_diff_numstat(wt) == expected


def test_land_diff_numstat_dirty_includes_untracked(arc_worktree: Path) -> None:
    wt = str(arc_worktree)
    (arc_worktree / "dirty.txt").write_text("new\n")
    (arc_worktree / "binary.bin").write_bytes(b"\x00\x01\x02")
    raw = land_diff_numstat(wt)
    assert "dirty.txt" in raw
    assert "binary.bin" in raw
    assert "-\t-\tbinary.bin" in raw or "binary.bin" in raw


def test_fingerprint_matches_sha256_of_land_diff_text_clean(arc_worktree: Path) -> None:
    wt = str(arc_worktree)
    body = land_diff_text(wt)
    assert hashlib.sha256(body.encode()).hexdigest() == land_fingerprint(wt)


def test_fingerprint_matches_sha256_of_land_diff_text_dirty(arc_worktree: Path) -> None:
    wt = str(arc_worktree)
    (arc_worktree / "uncommitted.py").write_text("x = 1\n")
    body = land_diff_text(wt)
    assert hashlib.sha256(body.encode()).hexdigest() == land_fingerprint(wt)


def test_land_diff_numstat_empty_when_no_changes(
    source_repo: Path, tmp_path: Path
) -> None:
    wt = tmp_path / "empty-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/empty", str(wt), "master", cwd=source_repo)
    assert land_diff_numstat(str(wt)) == ""
