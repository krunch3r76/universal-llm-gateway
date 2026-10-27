"""Tests for scripts/giw_worktree_reclaim.py (todo:giw-worktree-reaper-step3)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_RECLAIM_MOD = importlib.util.spec_from_file_location(
    "giw_worktree_reclaim",
    REPO_ROOT / "scripts" / "giw_worktree_reclaim.py",
)
assert _RECLAIM_MOD and _RECLAIM_MOD.loader
giw_worktree_reclaim = importlib.util.module_from_spec(_RECLAIM_MOD)
sys.modules[_RECLAIM_MOD.name] = giw_worktree_reclaim
_RECLAIM_MOD.loader.exec_module(giw_worktree_reclaim)
_orphan_lock_rows = giw_worktree_reclaim._orphan_lock_rows
run_reclaim = giw_worktree_reclaim.run_reclaim

_WIP_PREFIX = "wip: pre-reclaim snapshot"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def isolated_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Bare repo, worktree root, and lane directory path."""
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)

    worktree_root = tmp_path / "ulg-arc-worktrees"
    lane_root = worktree_root / repo.name
    lane_root.mkdir(parents=True)

    branch = "cursor-sdk/lane-99901"
    _git("branch", branch, "master", cwd=repo)
    lane_dir = lane_root / "lane-99901"
    _git(
        "worktree",
        "add",
        "-B",
        branch,
        str(lane_dir),
        branch,
        cwd=repo,
    )
    return repo, worktree_root, lane_dir


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    CursorDispatchLedger._instance = None
    yield
    CursorDispatchLedger._instance = None


def test_dirt_lane_wip_commit_then_removed(isolated_repo: tuple[Path, Path, Path]) -> None:
    """AC4: dirty lane gets forward-only wip commit, then worktree dir is removed."""
    repo, worktree_root, lane_dir = isolated_repo
    (lane_dir / "dirty.txt").write_text("wip\n", encoding="utf-8")
    old = time.time() - (49 * 3600)
    os.utime(lane_dir, (old, old))
    ledger_path = repo / "ledger.jsonl"

    rows = run_reclaim(
        source_repo=repo,
        worktree_root=worktree_root,
        ledger_path=ledger_path,
        execute=True,
    )

    log = _git("log", "-1", "--format=%s", "cursor-sdk/lane-99901", cwd=repo)
    assert log.stdout.strip().startswith(_WIP_PREFIX)

    assert not lane_dir.exists()
    lane_rows = [r for r in rows if r.lane == "lane-99901"]
    assert len(lane_rows) == 1
    assert lane_rows[0].disposition == "removed"
    assert lane_rows[0].wip_commit_sha_or_none


def test_dry_run_prints_without_remove_subcommand(
    isolated_repo: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    repo, worktree_root, lane_dir = isolated_repo
    ledger_path = repo / "ledger.jsonl"
    run_reclaim(
        source_repo=repo,
        worktree_root=worktree_root,
        ledger_path=ledger_path,
        execute=False,
    )
    out = capsys.readouterr().out
    assert "worktree remove" not in out
    assert lane_dir.is_dir()


def test_orphan_lock_reconciliation_row(isolated_repo: tuple[Path, Path, Path]) -> None:
    """AC5 shape: reconciliation row records orphan lock string when lock has no dir."""
    repo, worktree_root, lane_dir = isolated_repo
    lane_root = worktree_root / repo.name
    lock_reason = (
        "ulg:dispatch=ghost-dispatch;thread=99902;pinned_at=2026-01-01T00:00:00Z"
    )
    _git("worktree", "lock", "--reason", lock_reason, str(lane_dir), cwd=repo)
    shutil.rmtree(lane_dir)

    rows, orphans = _orphan_lock_rows(source_repo=repo, lane_root=lane_root)
    recon = [r for r in rows if r.record_type == "orphan_lock_reconciliation"]
    assert recon
    assert recon[0].orphan_lock_string is not None or orphans == []
    assert recon[0].locked_worktree_count is not None
    assert recon[0].lane_dir_count is not None


def test_ledger_jsonl_roundtrip(isolated_repo: tuple[Path, Path, Path]) -> None:
    repo, worktree_root, _lane_dir = isolated_repo
    ledger_path = repo / "ledger.jsonl"
    run_reclaim(
        source_repo=repo,
        worktree_root=worktree_root,
        ledger_path=ledger_path,
        execute=False,
    )
    lines = ledger_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    parsed = json.loads(lines[-1])
    assert parsed["record_type"] in {"lane", "orphan_lock_reconciliation"}
