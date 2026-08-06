"""Hermetic tests for lane-B salvage branch disposition (AC1–AC6)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.model_manager.ui.controller.busy_work_summary import (
    format_active_work_summary,
)
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_concurrency_meter import (
    lane_b_inventory_snapshot,
    reset_orphan_aged_emitted_registry,
)
from services.git_integration_worker.cursor_sdk_events import (
    reset_terminal_emitted_registry,
)
from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
    clear_disposition,
    get_disposition,
    list_dispositions,
    mark_lane_b_disposition,
    mark_lane_b_disposition_for_dispatch,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    mint_dispatch_worktree,
    resolve_master_branch_point,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    gc_merged_dispatch_branches,
    prune_dispatch_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    register_dispatch_worktree,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_orphan_aged_emitted_registry()
    reset_terminal_emitted_registry()
    yield
    CursorDispatchLedger._instance = None
    reset_orphan_aged_emitted_registry()
    reset_terminal_emitted_registry()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def _create_orphan_branch(
    source_repo: Path,
    *,
    dispatch_id: str,
    rel_path: str,
    body: str,
) -> tuple[str, str]:
    worktree_root = source_repo.parent / "worktrees"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    target = wt / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git("add", rel_path, cwd=wt)
    _git("commit", "-m", f"work {dispatch_id}", cwd=wt)
    prune_dispatch_worktree(dispatch_id=dispatch_id, source_repo=source_repo)
    return branch, branch_point


def test_ac1_meter_truth_two_orphan_branches_empty_registry(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC1: two orphan salvage branches + empty registry ⇒ branches_unlanded == 2."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "999999999")
    _create_orphan_branch(
        source_repo,
        dispatch_id="orph-a",
        rel_path="a.py",
        body="a\n",
    )
    _create_orphan_branch(
        source_repo,
        dispatch_id="orph-b",
        rel_path="b.py",
        body="b\n",
    )
    snap = lane_b_inventory_snapshot(source_repo=source_repo)
    assert snap["branches_unlanded"] == 2


def test_ac2_seat_dispose_marker_gc_reap_and_clear(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC2: abandon marker ⇒ GC deletes branch, reaped event, marker cleared."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "999999999")
    dispatch_id = "dispose-me"
    branch, _ = _create_orphan_branch(
        source_repo,
        dispatch_id=dispatch_id,
        rel_path="drop.py",
        body="drop\n",
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        mark_lane_b_disposition(
            branch_name=branch,
            reason="abandoned",
            dispatch_id=dispatch_id,
            tip_sha=tip,
        )
        deleted = gc_merged_dispatch_branches(source_repo=source_repo)

    assert deleted >= 1
    assert get_disposition(branch_name=branch) is None
    listing = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch not in listing
    reaped = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert reaped
    assert reaped[-1]["branch"] == branch
    assert reaped[-1]["tip_sha"] == tip
    assert reaped[-1]["reason"] == "abandoned"


def test_ac3_late_landing_cherry_reclaim_without_marker(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: patch-id equivalent on master ⇒ GC deletes orphan without marker."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "999999999")
    branch, _ = _create_orphan_branch(
        source_repo,
        dispatch_id="late-land",
        rel_path="landed.py",
        body="shipped\n",
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()
    (source_repo / "unrelated.md").write_text("move\n", encoding="utf-8")
    _git("add", "unrelated.md", cwd=source_repo)
    _git("commit", "-m", "master moved", cwd=source_repo)
    _git("cherry-pick", tip, cwd=source_repo)

    deleted = gc_merged_dispatch_branches(source_repo=source_repo)
    assert deleted >= 1
    listing = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch not in listing


def test_ac4_fail_closed_aged_unmarked_orphan_survives_gc(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC4: aged unique unmarked orphan survives sweeps; visibility only."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "0")
    branch, _ = _create_orphan_branch(
        source_repo,
        dispatch_id="keep-me",
        rel_path="unique.py",
        body="unique\n",
    )
    for _ in range(3):
        gc_merged_dispatch_branches(source_repo=source_repo)
    listing = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch in listing

    with patch(
        "services.git_integration_worker.cursor_sdk_lane_b_commit._patches_present_in_master",
        return_value=False,
    ):
        gc_merged_dispatch_branches(source_repo=source_repo)
    listing = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch in listing


def test_ac5_aged_orphan_surfaces_in_meter_and_busy_summary(
    source_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: aged orphan appears in inventory, active-work shape, busy summary."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "0")
    dispatch_id = "visible-orph"
    branch, _ = _create_orphan_branch(
        source_repo,
        dispatch_id=dispatch_id,
        rel_path="vis.py",
        body="vis\n",
    )
    tip = _git("rev-parse", branch, cwd=source_repo).stdout.strip()

    snap = lane_b_inventory_snapshot(source_repo=source_repo)
    assert snap["aged_orphans"]
    entry = snap["aged_orphans"][0]
    assert entry["branch"] == branch
    assert entry["tip_sha"] == tip
    assert entry["origin_dispatch_id"] == dispatch_id
    assert entry["age_s"] >= 0

    active_work = {
        "lane_b_regime": True,
        "lane_b": snap,
        "concurrency_stats": {
            "lane_b_aged_orphans": snap["aged_orphans"],
        },
    }
    summary = format_active_work_summary(active_work)
    assert branch in summary
    assert tip[:8] in summary
    assert dispatch_id[:8] in summary or dispatch_id in summary


def test_ac6_stale_marker_cleared_on_mint_register(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: mint/register clears stale marker; GC retains live registered branch."""
    monkeypatch.setenv("CURSOR_SDK_LANE_B_ORPHAN_VISIBILITY_TTL_S", "999999999")
    dispatch_id = "resurrect"
    branch, _ = _create_orphan_branch(
        source_repo,
        dispatch_id=dispatch_id,
        rel_path="old.py",
        body="old\n",
    )
    mark_lane_b_disposition(
        branch_name=branch,
        reason="abandoned",
        dispatch_id=dispatch_id,
    )
    assert get_disposition(branch_name=branch) is not None

    worktree_root = tmp_path / "worktrees"
    wt_path = worktree_root / f"cursor-sdk-{dispatch_id}"
    branch_point = resolve_master_branch_point(source_repo)
    register_dispatch_worktree(
        dispatch_id=dispatch_id,
        worktree_path=wt_path,
        branch_name=branch,
        branch_point=branch_point,
    )
    assert get_disposition(branch_name=branch) is None

    mark_lane_b_disposition(
        branch_name=branch,
        reason="abandoned",
        dispatch_id=dispatch_id,
    )
    deleted = gc_merged_dispatch_branches(source_repo=source_repo)
    assert deleted == 0
    listing = _git("branch", "--list", branch, cwd=source_repo).stdout
    assert branch in listing
    clear_disposition(branch_name=branch)


def test_mark_for_dispatch_skips_safe_to_delete(
    source_repo: Path, tmp_path: Path
) -> None:
    """Empty minted branch does not receive an abandon marker."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "empty-branch"
    mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    result = mark_lane_b_disposition_for_dispatch(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
        reason="abandoned",
    )
    assert result is None
    assert list_dispositions() == []
