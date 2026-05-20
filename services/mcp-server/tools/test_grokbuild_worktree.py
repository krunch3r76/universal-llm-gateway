"""Worktree-op handler tests (Phase 2c: worktree_create).

Phase 2d adds worktree_remove + worktree_busy tests in the same file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tools.grokbuild import grokbuild


@pytest.fixture
def worktree_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect WORKTREE_ROOT and ALLOWED_SOURCE_ROOT under tmp_path.

    Worktree handlers compute paths from the module constants; monkeypatching
    them lets tests exercise the real ``git worktree add`` flow against
    pytest-managed tmp dirs.
    """
    root = tmp_path / "worktrees"
    allowed = str(tmp_path)
    monkeypatch.setattr("tools._grokbuild_worktree.WORKTREE_ROOT", str(root))
    monkeypatch.setattr("tools._grokbuild_worktree.ALLOWED_SOURCE_ROOT", allowed)
    return root


@pytest.mark.asyncio
async def test_worktree_create_happy_path(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await grokbuild(
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
    assert "mcp.grokbuild.create.called" in signals
    assert "mcp.grokbuild.create.completed" in signals


@pytest.mark.asyncio
async def test_worktree_create_name_invalid_empty(
    git_repo: Path,
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await grokbuild(
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
    out = await grokbuild(
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
    out = await grokbuild(
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
    out = await grokbuild(
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
    out = await grokbuild(
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
    out = await grokbuild(
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

    out = await grokbuild(
        op="worktree_create",
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
    out = await grokbuild(
        op="worktree_create",
        name="cb1",
        branch="feat-new",
        source_repo=str(git_repo),
        create_branch=True,
    )
    assert out["status"] == "completed", out
    assert out["exit_code"] == 0
    assert out["metadata"]["create_branch"] is True
    assert out["metadata"]["start_point"] == ""
    import subprocess as _sp

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
    out = await grokbuild(
        op="worktree_create",
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
    out = await grokbuild(
        op="worktree_create",
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
    out = await grokbuild(
        op="worktree_create",
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
    out = await grokbuild(
        op="worktree_create",
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
    out = await grokbuild(
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
    return await grokbuild(
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

    out = await grokbuild(op="worktree_remove", name="r1")

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
    out = await grokbuild(op="worktree_remove", name="")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "name_invalid"


@pytest.mark.asyncio
async def test_worktree_remove_not_found(
    worktree_dirs: Path,
) -> None:
    out = await grokbuild(op="worktree_remove", name="never-existed")
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

    out = await grokbuild(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_dirty"
    # Worktree still exists since removal was refused.
    assert target.is_dir()


@pytest.mark.asyncio
async def test_worktree_remove_busy_rejection(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """An in-flight dispatch against a cwd under the worktree blocks removal."""
    from tools._grokbuild_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    # Simulate a concurrent dispatch holding the worktree cwd.
    assert await try_acquire_cwd(target) is True

    out = await grokbuild(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    assert "in-flight" in out["metadata"]["reason"]
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_busy_nested_cwd(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """A dispatch into a subdirectory of the worktree also blocks removal."""
    from tools._grokbuild_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await _create_then(git_repo)
    target = create["metadata"]["worktree_path"]
    nested = os.path.join(target, "subdir")
    os.makedirs(nested, exist_ok=True)
    assert await try_acquire_cwd(nested) is True

    out = await grokbuild(op="worktree_remove", name="r1")
    assert out["status"] == "rejected"
    assert out["metadata"]["reason_code"] == "worktree_busy"
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_remove_twice_second_not_found(
    git_repo: Path, worktree_dirs: Path
) -> None:
    create = await _create_then(git_repo)
    assert create["status"] == "completed"
    out1 = await grokbuild(op="worktree_remove", name="r1")
    assert out1["status"] == "completed"
    out2 = await grokbuild(op="worktree_remove", name="r1")
    assert out2["status"] == "rejected"
    assert out2["metadata"]["reason_code"] == "worktree_not_found"


# Phase 2e — worktree_list


@pytest.mark.asyncio
async def test_worktree_list_root_missing(
    worktree_dirs: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    """Root directory does not yet exist → empty list, status completed."""
    assert not worktree_dirs.exists()
    out = await grokbuild(op="worktree_list")
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
    out = await grokbuild(op="worktree_list")
    assert out["status"] == "completed"
    assert out["metadata"]["count"] == 0


@pytest.mark.asyncio
async def test_worktree_list_single(git_repo: Path, worktree_dirs: Path) -> None:
    create = await grokbuild(
        op="worktree_create", name="w1", branch="HEAD", source_repo=str(git_repo)
    )
    assert create["status"] == "completed"

    out = await grokbuild(op="worktree_list")
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
        c = await grokbuild(
            op="worktree_create", name=name, branch="HEAD", source_repo=str(git_repo)
        )
        assert c["status"] == "completed"
    out = await grokbuild(op="worktree_list")
    names = [e["name"] for e in out["metadata"]["worktrees"]]
    assert names == ["alpha", "mid", "zeta"]


@pytest.mark.asyncio
async def test_worktree_list_dirty_flag(git_repo: Path, worktree_dirs: Path) -> None:
    create = await grokbuild(
        op="worktree_create", name="d1", branch="HEAD", source_repo=str(git_repo)
    )
    (Path(create["metadata"]["worktree_path"]) / "tracked.txt").write_text("dirtied\n")
    out = await grokbuild(op="worktree_list")
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "d1")
    assert entry["dirty"] is True


@pytest.mark.asyncio
async def test_worktree_list_in_flight_flag(
    git_repo: Path, worktree_dirs: Path
) -> None:
    from tools._grokbuild_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await grokbuild(
        op="worktree_create", name="f1", branch="HEAD", source_repo=str(git_repo)
    )
    target = create["metadata"]["worktree_path"]
    nested = os.path.join(target, "sub")
    os.makedirs(nested, exist_ok=True)
    assert await try_acquire_cwd(nested) is True

    out = await grokbuild(op="worktree_list")
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "f1")
    assert entry["in_flight"] is True
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_list_dispatch_id_when_in_flight(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """When in_flight=True, dispatch_id surfaces the registry record."""
    from tools._grokbuild_registry import _reset_for_tests, try_acquire_cwd

    _reset_for_tests()
    create = await grokbuild(
        op="worktree_create", name="d1", branch="HEAD", source_repo=str(git_repo)
    )
    target = create["metadata"]["worktree_path"]
    assert await try_acquire_cwd(target, "uuid-disp-42") is True

    out = await grokbuild(op="worktree_list")
    entry = next(e for e in out["metadata"]["worktrees"] if e["name"] == "d1")
    assert entry["in_flight"] is True
    assert entry["dispatch_id"] == "uuid-disp-42"
    _reset_for_tests()


@pytest.mark.asyncio
async def test_worktree_list_dispatch_id_null_when_idle(
    git_repo: Path, worktree_dirs: Path
) -> None:
    """When in_flight=False, dispatch_id is null."""
    create = await grokbuild(
        op="worktree_create", name="idle1", branch="HEAD", source_repo=str(git_repo)
    )
    assert create["status"] == "completed"
    out = await grokbuild(op="worktree_list")
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
    out = await grokbuild(op="worktree_list")
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
    monkeypatch.setattr("tools._grokbuild_worktree.WORKTREE_ROOT", str(fake_root))

    def boom(_path: str) -> list[str]:
        raise OSError("simulated fs failure")

    monkeypatch.setattr("tools._grokbuild_worktree_list.os.listdir", boom)

    out = await grokbuild(op="worktree_list")
    assert out["status"] == "failed"
    assert out["metadata"]["reason_code"] == "worktree_root_unreachable"
    assert "simulated fs failure" in out["metadata"]["reason"]
    assert any(s == "mcp.grokbuild.list.failed" for s, _ in event_log)
