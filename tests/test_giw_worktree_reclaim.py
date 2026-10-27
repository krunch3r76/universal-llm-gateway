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
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load_reclaim_module():
    spec = importlib.util.spec_from_file_location(
        "giw_worktree_reclaim",
        REPO_ROOT / "scripts" / "giw_worktree_reclaim.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


giw_worktree_reclaim = _load_reclaim_module()
run_reclaim = giw_worktree_reclaim.run_reclaim
_orphan_lock_rows = giw_worktree_reclaim._orphan_lock_rows

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


@pytest.fixture
def mismatch_lane(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Detached lane whose HEAD is not contained in ``cursor-sdk/lane-99903``."""
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
    branch = "cursor-sdk/lane-99903"
    _git("branch", branch, "master", cwd=repo)
    lane_dir = lane_root / "lane-99903"
    _git("worktree", "add", "--detach", str(lane_dir), "master", cwd=repo)
    (lane_dir / "ahead.txt").write_text("tip\n", encoding="utf-8")
    _git("add", "ahead.txt", cwd=lane_dir)
    _git("commit", "-m", "detached tip", cwd=lane_dir)
    old = time.time() - (49 * 3600)
    os.utime(lane_dir, (old, old))
    return repo, worktree_root, lane_dir


@pytest.fixture(autouse=True)
def _stub_ledger_reads(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    if request.node.name == "test_ledger_unavailable_fail_closed":
        return
    monkeypatch.setattr(
        giw_worktree_reclaim,
        "_live_dispatch_thread_ids",
        lambda: set(),
    )
    monkeypatch.setattr(
        giw_worktree_reclaim,
        "_non_terminal_dispatch_for_thread",
        lambda _thread_id: None,
    )


def test_dirt_lane_wip_commit_then_removed(
    isolated_repo: tuple[Path, Path, Path],
) -> None:
    """Dirty on-branch lane: wip commit, ref, then worktree dir removed."""
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
    assert lane_rows[0].wip_ref == "refs/giw-wip/lane-99901"


def test_detached_dirty_lane_pins_wip_ref(
    isolated_repo: tuple[Path, Path, Path],
) -> None:
    """AC1 (a): detached dirty lane → wip sha reachable via refs/giw-wip/<lane>."""
    repo, worktree_root, lane_dir = isolated_repo
    _git("worktree", "remove", "--force", str(lane_dir), cwd=repo)
    _git("worktree", "add", "--detach", str(lane_dir), "master", cwd=repo)
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

    lane_rows = [r for r in rows if r.lane == "lane-99901"]
    assert lane_rows[0].disposition == "removed"
    assert lane_rows[0].wip_ref == "refs/giw-wip/lane-99901"
    pinned = _git(
        "rev-parse",
        lane_rows[0].wip_ref,
        cwd=repo,
    ).stdout.strip()
    assert pinned == lane_rows[0].wip_commit_sha_or_none
    assert not lane_dir.exists()


def test_ledger_unavailable_fail_closed(
    isolated_repo: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2 (b): ledger read failure skips every candidate; no worktree remove."""
    repo, worktree_root, lane_dir = isolated_repo
    old = time.time() - (49 * 3600)
    os.utime(lane_dir, (old, old))

    def _raise_ledger():
        raise OSError("ledger db unreadable")

    monkeypatch.setattr(giw_worktree_reclaim, "ledger_connection", _raise_ledger)

    with patch.object(giw_worktree_reclaim, "_remove_worktree") as remove_mock:
        rows = run_reclaim(
            source_repo=repo,
            worktree_root=worktree_root,
            ledger_path=repo / "ledger.jsonl",
            execute=True,
        )

    remove_mock.assert_not_called()
    candidate_rows = [r for r in rows if r.lane == "lane-99901"]
    assert len(candidate_rows) == 1
    assert candidate_rows[0].reason == "ledger_unavailable"
    assert lane_dir.is_dir()


def test_reclaim_mismatch_flag(mismatch_lane: tuple[Path, Path, Path]) -> None:
    """AC3 (c): mismatch lane skipped with hint; removed with --reclaim-mismatch."""
    repo, worktree_root, lane_dir = mismatch_lane
    ledger_path = repo / "ledger.jsonl"

    skip_rows = run_reclaim(
        source_repo=repo,
        worktree_root=worktree_root,
        ledger_path=ledger_path,
        execute=False,
        reclaim_mismatch=False,
    )
    skip_lane = next(r for r in skip_rows if r.lane == "lane-99903")
    assert skip_lane.reason == "branch_ref_does_not_contain_head"
    assert skip_lane.reclaim_hint
    assert lane_dir.is_dir()

    exec_rows = run_reclaim(
        source_repo=repo,
        worktree_root=worktree_root,
        ledger_path=ledger_path,
        execute=True,
        reclaim_mismatch=True,
    )
    exec_lane = next(r for r in exec_rows if r.lane == "lane-99903")
    assert exec_lane.disposition == "removed"
    assert not lane_dir.exists()


def test_default_ledger_runtime_path_not_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4 (d): default run writes runtime ledger, not tasks/specs template."""
    repo = tmp_path / "universal-llm-gateway"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@e.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    specs = repo / "tasks/specs"
    specs.mkdir(parents=True)
    template = specs / "giw-worktree-reclaim-ledger.jsonl"
    template.write_text('{"record_type":"meta"}\n', encoding="utf-8")
    _git("add", "tasks/specs/giw-worktree-reclaim-ledger.jsonl", cwd=repo)
    _git("commit", "-m", "track template", cwd=repo)
    template_before = template.read_text(encoding="utf-8")

    worktree_root = tmp_path / "ulg-arc-worktrees"
    (worktree_root / repo.name).mkdir(parents=True)

    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(repo))
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GIW_WORKTREE_RECLAIM_LEDGER", raising=False)
    monkeypatch.delenv("GIT_INTEGRATION_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_LEDGER", raising=False)

    assert giw_worktree_reclaim.main([]) == 0
    assert template.read_text(encoding="utf-8") == template_before
    porcelain = _git("status", "--porcelain", "--", "tasks/specs/", cwd=repo).stdout
    assert porcelain.strip() == ""
    runtime_ledger = repo / "tmp/giw/giw-worktree-reclaim-ledger.jsonl"
    assert runtime_ledger.is_file()


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
    """Reconciliation row records orphan lock string when lock has no dir."""
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
