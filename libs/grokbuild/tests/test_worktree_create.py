"""Worktree-create op handler tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grokbuild.worktree import worktree_create_op


@pytest.mark.asyncio
async def test_worktree_create_happy_path(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    import os

    out = await worktree_create_op(
        name="t1",
        branch="HEAD",
        source_repo=str(git_repo),
    )

    assert out["status"] == "completed"
    assert out["exit_code"] == 0
    expected_path = str(worktree_dirs / "t1")
    assert out["metadata"]["worktree_path"] == expected_path
    assert out["metadata"]["worktree_name"] == "t1"
    assert out["metadata"]["branch"] == "HEAD"
    assert os.path.isdir(expected_path)
    signals = [s for s, _ in event_log]
    assert "mcp.grokbuild.create.called" in signals
    assert "mcp.grokbuild.create.completed" in signals


@pytest.mark.asyncio
async def test_worktree_create_name_invalid_empty(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await worktree_create_op(
        name="",
        branch="HEAD",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"
    assert any(s.endswith(".create.rejected") for s, _ in event_log)


@pytest.mark.asyncio
async def test_worktree_create_name_invalid_slash(
    git_repo: Path, worktree_dirs: Path
) -> None:
    out = await worktree_create_op(
        name="foo/bar",
        branch="HEAD",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"


@pytest.mark.asyncio
async def test_worktree_create_name_invalid_dotdot(
    git_repo: Path, worktree_dirs: Path
) -> None:
    out = await worktree_create_op(
        name="..escape",
        branch="HEAD",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"


@pytest.mark.asyncio
async def test_worktree_create_source_repo_invalid_outside_root(
    git_repo: Path, worktree_dirs: Path, tmp_path: Path
) -> None:
    """source_repo outside the allowed root rejects.

    Use ``/tmp`` which is outside the monkeypatched allowed_root (tmp_path).
    """
    out = await worktree_create_op(
        name="t1",
        branch="HEAD",
        source_repo="/etc",
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "source_repo_invalid"


@pytest.mark.asyncio
async def test_worktree_create_source_repo_not_a_git_repo(
    worktree_dirs: Path, tmp_path: Path
) -> None:
    not_a_repo = tmp_path / "plain-dir"
    not_a_repo.mkdir()
    out = await worktree_create_op(
        name="t1",
        branch="HEAD",
        source_repo=str(not_a_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "source_repo_invalid"


@pytest.mark.asyncio
async def test_worktree_create_branch_not_found(
    git_repo: Path, worktree_dirs: Path
) -> None:
    out = await worktree_create_op(
        name="t1",
        branch="nonexistent-branch-xyz",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "branch_not_found"


@pytest.mark.asyncio
async def test_worktree_create_worktree_exists(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """Target path already exists → reject with worktree_exists."""
    target = worktree_dirs / "t1"
    target.mkdir(parents=True)

    out = await worktree_create_op(
        name="t1",
        branch="HEAD",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_exists"


@pytest.mark.asyncio
async def test_worktree_create_with_create_branch_default_start_point(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """create_branch=True with no start_point → new branch from HEAD."""
    import subprocess as _sp

    out = await worktree_create_op(
        name="cb1",
        branch="feat-new",
        source_repo=str(git_repo),
        create_branch=True,
    )
    assert out["status"] == "completed", out
    assert out["exit_code"] == 0
    assert out["metadata"]["create_branch"] is True
    assert out["metadata"]["start_point"] == ""
    rc = _sp.run(
        ["git", "-C", str(git_repo), "rev-parse", "--verify", "refs/heads/feat-new"],
        capture_output=True,
    )
    assert rc.returncode == 0
    signals = [s for s, _ in event_log]
    assert "mcp.grokbuild.create.completed" in signals


@pytest.mark.asyncio
async def test_worktree_create_with_create_branch_explicit_start_point(
    git_repo: Path,
    worktree_dirs: Path,
) -> None:
    """create_branch=True with explicit start_point (commit SHA) works."""
    import subprocess as _sp

    sha = _sp.run(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    out = await worktree_create_op(
        name="cb2",
        branch="feat-sha",
        source_repo=str(git_repo),
        create_branch=True,
        start_point=sha,
    )
    assert out["status"] == "completed", out
    assert out["metadata"]["start_point"] == sha
    head = _sp.run(
        ["git", "-C", str(git_repo), "rev-parse", "refs/heads/feat-sha"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head == sha


@pytest.mark.asyncio
async def test_worktree_create_branch_exists(
    git_repo: Path,
    worktree_dirs: Path,
) -> None:
    """create_branch=True against a pre-existing branch rejects."""
    import subprocess as _sp

    _sp.run(
        ["git", "-C", str(git_repo), "branch", "already-here"],
        check=True,
        capture_output=True,
    )
    out = await worktree_create_op(
        name="cb3",
        branch="already-here",
        source_repo=str(git_repo),
        create_branch=True,
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "branch_exists"


@pytest.mark.asyncio
async def test_worktree_create_start_point_not_found(
    git_repo: Path,
    worktree_dirs: Path,
) -> None:
    out = await worktree_create_op(
        name="cb4",
        branch="feat-bad-sp",
        source_repo=str(git_repo),
        create_branch=True,
        start_point="no-such-ref-xyz",
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "start_point_not_found"


@pytest.mark.asyncio
async def test_worktree_create_branch_checked_out_elsewhere(
    git_repo: Path,
    worktree_dirs: Path,
) -> None:
    """create_branch=False against a branch checked out in another worktree rejects.

    The source_repo itself has its primary branch checked out — attempting
    to re-check-it-out in a sibling worktree must reject with a clear code
    instead of hitting the bare git error.
    """
    import subprocess as _sp

    primary = _sp.run(
        ["git", "-C", str(git_repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    out = await worktree_create_op(
        name="cb5",
        branch=primary,
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "branch_checked_out_elsewhere"
    assert str(git_repo) in out["metadata"]["reason"]


@pytest.mark.asyncio
async def test_worktree_create_branch_required(
    git_repo: Path, worktree_dirs: Path
) -> None:
    out = await worktree_create_op(
        name="t1",
        branch="",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"
    assert "branch" in out["metadata"]["reason"]
