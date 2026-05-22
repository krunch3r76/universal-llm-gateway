"""Worktree-remove op handler tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from grokbuild.worktree import worktree_create_op
from grokbuild.worktree_remove import worktree_remove_op


async def _create_then(git_repo: Path) -> dict[str, Any]:
    """Helper: create a worktree against git_repo, return the envelope."""
    return await worktree_create_op(
        name="r1",
        branch="HEAD",
        source_repo=str(git_repo),
    )


@pytest.mark.asyncio
async def test_worktree_remove_happy_path(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    create = await _create_then(git_repo)
    assert create["status"] == "completed"
    target = create["metadata"]["worktree_path"]

    out = await worktree_remove_op(name="r1")

    assert out["status"] == "completed"
    assert out["exit_code"] == 0
    assert out["metadata"]["worktree_name"] == "r1"
    assert out["metadata"]["worktree_path"] == target
    assert not os.path.isdir(target)
    signals = [s for s, _ in event_log]
    assert "mcp.grokbuild.remove.called" in signals
    assert "mcp.grokbuild.remove.completed" in signals


@pytest.mark.asyncio
async def test_worktree_remove_name_invalid(
    worktree_dirs: Path,
) -> None:
    out = await worktree_remove_op(name="")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"


@pytest.mark.asyncio
async def test_worktree_remove_not_found(
    worktree_dirs: Path,
) -> None:
    out = await worktree_remove_op(name="never-existed")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_not_found"


@pytest.mark.asyncio
async def test_worktree_remove_dirty_rejection(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """A worktree with uncommitted changes refuses removal (no --force in Phase 2)."""
    create = await _create_then(git_repo)
    target = Path(create["metadata"]["worktree_path"])
    (target / "tracked.txt").write_text("dirtied\n")

    out = await worktree_remove_op(name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_dirty"
    assert target.is_dir()


@pytest.mark.asyncio
async def test_worktree_remove_busy_rejection(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """An in-flight dispatch against a cwd under the worktree blocks removal."""
    from grokbuild.registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    assert await try_acquire_cwd(target) is True

    out = await worktree_remove_op(name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    assert "in-flight" in out["metadata"]["reason"]
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_busy_nested_cwd(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """A dispatch into a subdirectory of the worktree also blocks removal."""
    from grokbuild.registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    nested = os.path.join(target, "subdir")
    os.makedirs(nested, exist_ok=True)
    assert await try_acquire_cwd(nested) is True

    out = await worktree_remove_op(name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_twice_second_not_found(
    git_repo: Path, worktree_dirs: Path
) -> None:
    create = await _create_then(git_repo)
    assert create["status"] == "completed"
    out1 = await worktree_remove_op(name="r1")
    assert out1["status"] == "completed"
    out2 = await worktree_remove_op(name="r1")
    assert out2["status"] == "rejected"
    assert out2["metadata"]["reason_code"] == "worktree_not_found"
