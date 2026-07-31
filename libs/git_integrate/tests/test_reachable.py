"""Unit tests for git_cas.commit_exists + is_reachable_from_master.

The reachability primitive backs the cortex landed-claim audit detector
(thread 1153). It reconciles a SHA against LOCAL refs/heads/master:
  - reachable: SHA on master (master tip itself, or any ancestor)
  - not-ancestor: a real commit (arc tip, shared ODB) not yet on master
  - phantom: SHA absent from the repo
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from git_integrate.git_cas import commit_exists, is_reachable_from_master


def _ref_sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_master_tip_is_reachable(source_repo: Path) -> None:
    master_sha = _ref_sha(source_repo, "refs/heads/master")
    assert await commit_exists(str(source_repo), master_sha)
    assert await is_reachable_from_master(str(source_repo), master_sha)


@pytest.mark.asyncio
async def test_arc_commit_not_ancestor_of_master(
    source_repo: Path, arc_worktree: Path
) -> None:
    """arc_worktree has a commit ahead of master; it shares the ODB (so it
    exists) but is NOT an ancestor of refs/heads/master (not yet landed)."""
    arc_head = _ref_sha(arc_worktree, "HEAD")
    assert await commit_exists(str(source_repo), arc_head)
    assert not await is_reachable_from_master(str(source_repo), arc_head)


@pytest.mark.asyncio
async def test_phantom_sha_absent(source_repo: Path) -> None:
    phantom = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert not await commit_exists(str(source_repo), phantom)
    assert not await is_reachable_from_master(str(source_repo), phantom)
