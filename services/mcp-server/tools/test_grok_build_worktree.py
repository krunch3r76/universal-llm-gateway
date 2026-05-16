"""Worktree-op handler tests (Phase 2c: worktree_create).

Phase 2d adds worktree_remove + worktree_busy tests in the same file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tools.grok_build import grok_build


@pytest.fixture
def worktree_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect WORKTREE_ROOT and ALLOWED_SOURCE_ROOT under tmp_path.

    Worktree handlers compute paths from the module constants; monkeypatching
    them lets tests exercise the real ``git worktree add`` flow against
    pytest-managed tmp dirs.
    """
    root = tmp_path / "worktrees"
    allowed = str(tmp_path)
    monkeypatch.setattr("tools._grok_build_worktree.WORKTREE_ROOT", str(root))
    monkeypatch.setattr("tools._grok_build_worktree.ALLOWED_SOURCE_ROOT", allowed)
    return root


@pytest.mark.asyncio
async def test_worktree_create_happy_path(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await grok_build(
        op="worktree_create",
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
    assert "mcp.grok.build.create.called" in signals
    assert "mcp.grok.build.create.completed" in signals


@pytest.mark.asyncio
async def test_worktree_create_name_invalid_empty(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await grok_build(
        op="worktree_create",
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
    out = await grok_build(
        op="worktree_create",
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
    out = await grok_build(
        op="worktree_create",
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
    out = await grok_build(
        op="worktree_create",
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
    out = await grok_build(
        op="worktree_create",
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
    out = await grok_build(
        op="worktree_create",
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

    out = await grok_build(
        op="worktree_create",
        name="t1",
        branch="HEAD",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_exists"


@pytest.mark.asyncio
async def test_worktree_create_branch_required(
    git_repo: Path, worktree_dirs: Path
) -> None:
    out = await grok_build(
        op="worktree_create",
        name="t1",
        branch="",
        source_repo=str(git_repo),
    )
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"
    assert "branch" in out["metadata"]["reason"]


# Phase 2d — worktree_remove


@pytest.mark.asyncio
async def _create_then(
    git_repo: Path,
) -> dict[str, Any]:
    """Helper: create a worktree against git_repo, return the envelope."""
    return await grok_build(
        op="worktree_create",
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

    out = await grok_build(op="worktree_remove", name="r1")

    assert out["status"] == "completed"
    assert out["exit_code"] == 0
    assert out["metadata"]["worktree_name"] == "r1"
    assert out["metadata"]["worktree_path"] == target
    assert not os.path.isdir(target)
    signals = [s for s, _ in event_log]
    assert "mcp.grok.build.remove.called" in signals
    assert "mcp.grok.build.remove.completed" in signals


@pytest.mark.asyncio
async def test_worktree_remove_name_invalid(
    worktree_dirs: Path,
) -> None:
    out = await grok_build(op="worktree_remove", name="")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"


@pytest.mark.asyncio
async def test_worktree_remove_not_found(
    worktree_dirs: Path,
) -> None:
    out = await grok_build(op="worktree_remove", name="never-existed")
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

    out = await grok_build(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_dirty"
    # Worktree still exists since removal was refused.
    assert target.is_dir()


@pytest.mark.asyncio
async def test_worktree_remove_busy_rejection(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """An in-flight dispatch against a cwd under the worktree blocks removal."""
    from tools._grok_build_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    # Simulate a concurrent dispatch holding the worktree cwd.
    assert await try_acquire_cwd(target) is True

    out = await grok_build(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    assert "in-flight" in out["metadata"]["reason"]
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_busy_nested_cwd(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """A dispatch into a subdirectory of the worktree also blocks removal."""
    from tools._grok_build_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    nested = os.path.join(target, "subdir")
    os.makedirs(nested, exist_ok=True)
    assert await try_acquire_cwd(nested) is True

    out = await grok_build(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_twice_second_not_found(
    git_repo: Path, worktree_dirs: Path
) -> None:
    create = await _create_then(git_repo)
    assert create["status"] == "completed"
    out1 = await grok_build(op="worktree_remove", name="r1")
    assert out1["status"] == "completed"
    out2 = await grok_build(op="worktree_remove", name="r1")
    assert out2["status"] == "rejected"
    assert out2["metadata"]["reason_code"] == "worktree_not_found"
