"""Unit tests for land_op orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from git_integrate.git_cas import diff_sha256, land_fingerprint
from git_integrate.integrate import integrate_op
from git_integrate.land import land_op
from git_integrate.schema import (
    EMPTY_DIFF_SHA256,
    RC_DIRTY_WORKTREE,
    RC_NOTHING_TO_LAND,
    RC_UNCOMMITTED_NO_MESSAGE,
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


def _passing_gate() -> list[str]:
    return ["true"]


@pytest.fixture
def dirty_arc_worktree(tmp_path: Path, source_repo: Path) -> Path:
    """Arc worktree with uncommitted changes only (no extra commits)."""
    wt = tmp_path / "worktrees" / "dirty-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/dirty-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / "uncommitted.py").write_text("# uncommitted\n")
    return wt


@pytest.mark.asyncio
async def test_integrate_on_dirty_tree_rejects_and_preserves_work(
    source_repo: Path,
    dirty_arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    sha = diff_sha256(str(dirty_arc_worktree))
    assert sha == EMPTY_DIFF_SHA256

    out = await integrate_op(
        arc="dirty-arc",
        phase="phase-3",
        worktree_path=str(dirty_arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_DIRTY_WORKTREE
    assert (dirty_arc_worktree / "uncommitted.py").exists()
    assert "git.integrate.requested" not in [s for s, _ in event_log]


@pytest.mark.asyncio
async def test_land_dirty_happy_path(
    source_repo: Path,
    dirty_arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    fingerprint = land_fingerprint(str(dirty_arc_worktree))
    assert fingerprint != EMPTY_DIFF_SHA256

    out = await land_op(
        arc="dirty-arc",
        phase="phase-3",
        worktree_path=str(dirty_arc_worktree),
        approval="approved",
        expected_diff_sha256=fingerprint,
        commit_message="land uncommitted work",
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
        remove_worktree=False,
    )

    assert out["status"] == "completed", out
    assert out["committed"] is True
    assert out["commit_sha"]
    assert out["master_sha"]
    # Ground-truth equality this whole episode is about: the reported
    # master_sha IS the advanced tip of refs/heads/master in source_repo —
    # not telemetry, the ref itself (thread 1153).
    assert out["master_sha"] == _ref_sha(source_repo, "refs/heads/master")
    assert out["landed_ref"] == "refs/heads/master"
    signals = [s for s, _ in event_log]
    assert "git.land.requested" in signals
    assert "git.land.completed" in signals
    assert "git.commit.created" in signals


@pytest.mark.asyncio
async def test_land_dirty_no_commit_message_rejects(
    source_repo: Path,
    dirty_arc_worktree: Path,
) -> None:
    fingerprint = land_fingerprint(str(dirty_arc_worktree))
    out = await land_op(
        arc="dirty-arc",
        phase="phase-3",
        worktree_path=str(dirty_arc_worktree),
        approval="approved",
        expected_diff_sha256=fingerprint,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )
    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_UNCOMMITTED_NO_MESSAGE


@pytest.mark.asyncio
async def test_land_empty_diff_rejects(
    source_repo: Path,
    tmp_path: Path,
) -> None:
    """Arc branch identical to master with no changes → nothing_to_land."""
    wt = tmp_path / "worktrees" / "empty-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/empty-arc", str(wt), "master", cwd=source_repo)
    fingerprint = land_fingerprint(str(wt))
    assert fingerprint == EMPTY_DIFF_SHA256

    out = await land_op(
        arc="empty-arc",
        phase="phase-3",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=fingerprint,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )
    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_NOTHING_TO_LAND
