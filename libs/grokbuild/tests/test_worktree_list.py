"""Worktree-list op handler tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from grokbuild.worktree import worktree_create_op
from grokbuild.worktree_list import worktree_list_op


@pytest.mark.asyncio
async def test_worktree_list_root_missing(
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """Root directory does not yet exist → empty list, status completed."""
    assert not worktree_dirs.exists()
    out = await worktree_list_op()
    assert out["status"] == "completed"
    assert out["metadata"]["count"] == 0
    assert out["metadata"]["worktrees"] == []
    assert out["metadata"]["worktree_root"] == str(worktree_dirs)
    signals = [s for s, _ in event_log]
    assert "mcp.grokbuild.list.called" in signals
    assert "mcp.grokbuild.list.completed" in signals


@pytest.mark.asyncio
async def test_worktree_list_empty_root(
    worktree_dirs: Path,
) -> None:
    worktree_dirs.mkdir(parents=True)
    out = await worktree_list_op()
    assert out["status"] == "completed"
    assert out["metadata"]["count"] == 0


@pytest.mark.asyncio
async def test_worktree_list_single(git_repo: Path, worktree_dirs: Path) -> None:
    create = await worktree_create_op(
        name="w1", branch="HEAD", source_repo=str(git_repo)
    )
    assert create["status"] == "completed"

    out = await worktree_list_op()
    assert out["status"] == "completed"
    assert out["metadata"]["count"] == 1
    entry = out["metadata"]["worktrees"][0]
    assert entry["name"] == "w1"
    assert entry["path"] == str(worktree_dirs / "w1")
    assert entry["valid"] is True
    assert entry["dirty"] is False
    assert entry["in_flight"] is False
    assert len(entry["head_sha"]) == 40
    assert entry["branch"]


@pytest.mark.asyncio
async def test_worktree_list_multi_sorted(git_repo: Path, worktree_dirs: Path) -> None:
    for name in ("zeta", "alpha", "mid"):
        c = await worktree_create_op(
            name=name, branch="HEAD", source_repo=str(git_repo)
        )
        assert c["status"] == "completed"
    out = await worktree_list_op()
    names = [e["name"] for e in out["metadata"]["worktrees"]]
    assert names == ["alpha", "mid", "zeta"]


@pytest.mark.asyncio
async def test_worktree_list_dirty_flag(git_repo: Path, worktree_dirs: Path) -> None:
    create = await worktree_create_op(
        name="d1", branch="HEAD", source_repo=str(git_repo)
    )
    (Path(create["metadata"]["worktree_path"]) / "tracked.txt").write_text("dirtied\n")
    out = await worktree_list_op()
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "d1")
    assert entry["dirty"] is True


@pytest.mark.asyncio
async def test_worktree_list_in_flight_flag(
    git_repo: Path, worktree_dirs: Path
) -> None:
    from grokbuild.registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await worktree_create_op(
        name="f1", branch="HEAD", source_repo=str(git_repo)
    )
    target = create["metadata"]["worktree_path"]
    nested = os.path.join(target, "sub")
    os.makedirs(nested, exist_ok=True)
    assert await try_acquire_cwd(nested) is True

    out = await worktree_list_op()
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "f1")
    assert entry["in_flight"] is True
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_list_dispatch_id_when_in_flight(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """When in_flight=True, dispatch_id surfaces the registry record."""
    from grokbuild.registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await worktree_create_op(
        name="d1", branch="HEAD", source_repo=str(git_repo)
    )
    target = create["metadata"]["worktree_path"]
    assert await try_acquire_cwd(target, "uuid-disp-42") is True

    out = await worktree_list_op()
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "d1")
    assert entry["in_flight"] is True
    assert entry["dispatch_id"] == "uuid-disp-42"
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_list_dispatch_id_null_when_idle(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """When in_flight=False, dispatch_id is null."""
    create = await worktree_create_op(
        name="idle1", branch="HEAD", source_repo=str(git_repo)
    )
    assert create["status"] == "completed"
    out = await worktree_list_op()
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "idle1")
    assert entry["in_flight"] is False
    assert entry["dispatch_id"] is None


@pytest.mark.asyncio
async def test_worktree_list_skips_non_git_dirs(
    worktree_dirs: Path,
) -> None:
    """A stray non-git directory under the root surfaces with valid=False."""
    worktree_dirs.mkdir(parents=True)
    (worktree_dirs / "stray").mkdir()
    out = await worktree_list_op()
    assert out["status"] == "completed"
    assert out["metadata"]["count"] == 1
    entry = out["metadata"]["worktrees"][0]
    assert entry["name"] == "stray"
    assert entry["valid"] is False
    assert entry["dirty"] is False


@pytest.mark.asyncio
async def test_worktree_list_root_unreachable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """OSError on enumeration maps to worktree_root_unreachable."""
    fake_root = tmp_path / "wt-unreach"
    fake_root.mkdir()
    monkeypatch.setattr("grokbuild.worktree.WORKTREE_ROOT", str(fake_root))

    def boom(_path: str) -> list[str]:
        raise OSError("simulated fs failure")

    monkeypatch.setattr("grokbuild.worktree_list.os.listdir", boom)

    out = await worktree_list_op()
    assert out["status"] == "failed"
    assert out["metadata"]["reason_code"] == "worktree_root_unreachable"
    assert "simulated fs failure" in out["metadata"]["reason"]
    assert any(s == "mcp.grokbuild.list.failed" for s, _ in event_log)
