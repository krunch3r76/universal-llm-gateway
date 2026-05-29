"""Unit tests for git_cas pure mechanics."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.git_cas import (
    abort_merge,
    advance_master_cas,
    current_sha,
    diff_sha256,
    fetch_master,
    merge_master_into,
    reset_hard_to,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _ref_sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_current_sha_resolves_head(arc_worktree: Path) -> None:
    expected = _head_sha(arc_worktree)
    got = await current_sha(str(arc_worktree), "HEAD")
    assert got == expected


@pytest.mark.asyncio
async def test_current_sha_missing_ref(arc_worktree: Path) -> None:
    got = await current_sha(str(arc_worktree), "refs/heads/nonexistent-branch-xyz")
    assert got == ""


@pytest.mark.asyncio
async def test_diff_sha256_stable(arc_worktree: Path) -> None:
    sha1 = diff_sha256(str(arc_worktree))
    sha2 = diff_sha256(str(arc_worktree))
    assert sha1 == sha2
    assert len(sha1) == 64  # sha256 hex digest


@pytest.mark.asyncio
async def test_diff_sha256_changes_with_commit(arc_worktree: Path) -> None:
    before = diff_sha256(str(arc_worktree))
    _git("config", "user.email", "test@example.com", cwd=arc_worktree)
    _git("config", "user.name", "Test", cwd=arc_worktree)
    (arc_worktree / "extra.py").write_text("# extra\n")
    _git("add", "extra.py", cwd=arc_worktree)
    _git("commit", "-m", "add extra", cwd=arc_worktree)
    after = diff_sha256(str(arc_worktree))
    assert before != after


@pytest.mark.asyncio
async def test_fetch_master_nonfatal_on_no_remote(arc_worktree: Path) -> None:
    # fetch_master must not raise even with no origin remote
    await fetch_master(str(arc_worktree))


@pytest.mark.asyncio
async def test_merge_master_into_clean(source_repo: Path, arc_worktree: Path) -> None:
    result = await merge_master_into(str(arc_worktree))
    # arc/test-arc already has master merged in (created from master) —
    # master has no new commits so this is a no-op merge (already up to date)
    assert not result.conflict


@pytest.mark.asyncio
async def test_merge_master_into_conflict(tmp_path: Path, source_repo: Path) -> None:
    """Arc and master modify the same line → conflict → abort leaves repo clean."""
    wt = tmp_path / "worktrees" / "conflict-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(
        "worktree", "add", "-b", "arc/conflict-arc", str(wt), "master", cwd=source_repo
    )
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)

    # Arc branch modifies README
    (wt / "README.md").write_text("arc change\n")
    _git("add", "README.md", cwd=wt)
    _git("commit", "-m", "arc edit", cwd=wt)

    # master also modifies README (park checkout lets us do this)
    _git("checkout", "master", cwd=source_repo)
    (source_repo / "README.md").write_text("master change\n")
    _git("add", "README.md", cwd=source_repo)
    _git("commit", "-m", "master edit", cwd=source_repo)
    _git("checkout", "_integration_parked", cwd=source_repo)

    result = await merge_master_into(str(wt))
    assert result.conflict

    # abort leaves the worktree clean
    await abort_merge(str(wt))
    status = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_reset_hard_to_restores_tip(arc_worktree: Path) -> None:
    tip = _head_sha(arc_worktree)
    _git("config", "user.email", "test@example.com", cwd=arc_worktree)
    _git("config", "user.name", "Test", cwd=arc_worktree)
    (arc_worktree / "tmp_file.py").write_text("# tmp\n")
    _git("add", "tmp_file.py", cwd=arc_worktree)
    _git("commit", "-m", "tmp commit", cwd=arc_worktree)
    assert _head_sha(arc_worktree) != tip

    await reset_hard_to(str(arc_worktree), tip)
    assert _head_sha(arc_worktree) == tip


@pytest.mark.asyncio
async def test_advance_master_cas_succeeds(
    source_repo: Path, arc_worktree: Path
) -> None:
    master_before = _ref_sha(source_repo, "refs/heads/master")
    result = await advance_master_cas(
        str(source_repo), str(arc_worktree), expected=master_before
    )
    assert not result.non_ff
    assert result.new_sha == _head_sha(arc_worktree)
    # master ref is now the arc HEAD
    assert _ref_sha(source_repo, "refs/heads/master") == result.new_sha


@pytest.mark.asyncio
async def test_advance_master_cas_fails_on_stale_expected(
    source_repo: Path, arc_worktree: Path
) -> None:
    stale_sha = "0" * 40
    result = await advance_master_cas(
        str(source_repo), str(arc_worktree), expected=stale_sha
    )
    assert result.non_ff


@pytest.mark.asyncio
async def test_advance_master_never_touches_live_working_tree(
    source_repo: Path, arc_worktree: Path
) -> None:
    """Assert integration advances only via update-ref, not via checkout/merge."""
    master_before = _ref_sha(source_repo, "refs/heads/master")

    # Record file state of source_repo working tree before CAS
    files_before = {
        p.name: p.read_text()
        for p in source_repo.iterdir()
        if p.is_file() and not p.name.startswith(".")
    }

    adv = await advance_master_cas(
        str(source_repo), str(arc_worktree), expected=master_before
    )
    assert not adv.non_ff

    # Working tree files in source_repo must not have changed
    files_after = {
        p.name: p.read_text()
        for p in source_repo.iterdir()
        if p.is_file() and not p.name.startswith(".")
    }
    assert files_before == files_after
