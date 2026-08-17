"""Bound-invariant falsifiers — S1b worktree mint/GC (F-A4, F-A5, AC1)."""

from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_restart_orphan import (
    salvage_restart_survivor_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    reap_orphan_worktrees,
    resolve_admit_binding,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    is_reapable_dispatch_status,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
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
    yield
    CursorDispatchLedger._instance = None


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


def test_falsifier_f_a5_concurrent_mint_no_lock_contention(
    source_repo: Path, tmp_path: Path
) -> None:
    """F-A5: N concurrent isolated mints do not fail on .git lock contention."""
    worktree_root = tmp_path / "worktrees"
    n = 4
    errors: list[Exception] = []
    paths: list[Path] = []

    def _mint(i: int) -> Path:
        return mint_dispatch_worktree(
            source_repo=source_repo,
            worktree_root=worktree_root,
            dispatch_id=f"conc-{i}",
        )

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_mint, i) for i in range(n)]
        for fut in as_completed(futures):
            try:
                paths.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    assert not errors, errors
    assert len(paths) == n
    assert len({str(p.resolve()) for p in paths}) == n


def test_falsifier_f_a4_prune_on_terminal_and_reaper_path(
    source_repo: Path, tmp_path: Path
) -> None:
    """F-A4: S1b ships prune-on-terminal and a reaper path for orphan worktrees."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "fa4-dispatch"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id="exec",
        message="hi",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint="fp",
        execution_id="exec",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="t1",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="consult",
        worker_instance="worker-a",
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="failed")
    assert not maybe_prune_worktree_on_terminal(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    ).pruned
    assert wt.is_dir()

    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        unregister_lane_worktree,
    )

    orphan_id = "fa4-orphan"
    orphan_wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=orphan_id,
    )
    unregister_lane_worktree(thread_id=orphan_id)
    assert orphan_wt.is_dir()
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.worktrees_reconciled >= 1
    assert not orphan_wt.exists()


def test_falsifier_ac1_lane_b_mints_worktree_under_root(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC1: Lane-B dispatch mints a worktree under worktree_root (not shared cwd)."""
    worktree_root = tmp_path / "worktrees"
    shared = tmp_path / "shared-master-parent"
    shared.mkdir()
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id="ac1-b",
        execution_id="exec-ac1",
        message="hello",
        worktree_isolated=True,
    )
    workspace, lease_key = resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=shared,
        lane="B",
    )
    assert workspace != shared
    assert workspace.is_dir()
    assert str(workspace.resolve()) == lease_key
    assert workspace.relative_to(worktree_root.resolve())


def test_falsifier_ac_s6_1_reaper_salvages_dirty_terminal(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S6.1: reaper leaves an unmerged dirty lane tree in place."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s6-dirty-reap"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "dirty.py").write_text("payload\n", encoding="utf-8")
    branch = f"cursor-sdk/lane-{dispatch_id}"
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id="exec",
        message="hi",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint="fp",
        execution_id="exec",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="t1",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="failed")
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.reaped == 0
    assert wt.is_dir()
    assert (wt / "dirty.py").read_text(encoding="utf-8") == "payload\n"
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout


def test_falsifier_ac_s6_2_restart_survivor_salvages_before_terminal(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S6.2: restart survivor salvage commits dirty work; the lane tree stays."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s6-restart"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    rel = "survivor.py"
    (wt / rel).write_text("survive\n", encoding="utf-8")
    branch = f"cursor-sdk/lane-{dispatch_id}"
    result = salvage_restart_survivor_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert not result.pruned
    assert result.salvaged
    assert result.branch_retained
    assert wt.is_dir()
    show = subprocess.run(
        ["git", "-C", str(source_repo), "show", f"{branch}:{rel}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert show.returncode == 0
    assert "survive" in show.stdout


def test_falsifier_ac_s6_3_failed_and_cancelled_statuses_reapable() -> None:
    """AC-S6.3: failed and cancelled ledger statuses are inside the reapable set."""
    assert is_reapable_dispatch_status("failed")
    assert is_reapable_dispatch_status("cancelled")
    assert not is_reapable_dispatch_status("running")
    assert not is_reapable_dispatch_status("parked_waiting")
    assert not is_reapable_dispatch_status(None)


def test_falsifier_ac_s6_4_parked_waiting_parent_not_reaped(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S6.4: parked_waiting Lane-B parent is never reaped while child runs."""
    worktree_root = tmp_path / "worktrees"
    parent_id = "s6-parent"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=parent_id,
    )
    lease_key = str(wt.resolve())
    ledger = CursorDispatchLedger.instance()
    parent_req = CursorDispatchRequest(
        thread_id="t-parent",
        model="cursor/composer-2.5",
        dispatch_id=parent_id,
        execution_id="exec-parent",
        message="parent",
        worktree_isolated=True,
    )
    ledger.admit(
        req=parent_req,
        fingerprint="fp-parent",
        execution_id="exec-parent",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=parent_id,
            thread_id="t-parent",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=lease_key,
        contract="implement",
        worker_instance="worker-a",
    )
    ledger.mark_running(dispatch_id=parent_id)
    child_id = "s6-child"
    child_req = CursorDispatchRequest(
        thread_id="t-child",
        model="cursor/composer-2.5",
        dispatch_id=child_id,
        execution_id="exec-child",
        message="child",
        worktree_isolated=True,
        nest_under=parent_id,
    )
    ledger.admit(
        req=child_req,
        fingerprint="fp-child",
        execution_id="exec-child",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=child_id,
            thread_id="t-child",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=lease_key,
        contract="implement",
        worker_instance="worker-a",
        nest_under=parent_id,
    )
    with CursorDispatchLedger.instance()._connect() as conn:
        parent = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    assert parent is not None
    assert parent["status"] == "parked_waiting"
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.reaped == 0
    assert wt.is_dir()


def test_falsifier_ac_s6_5_hand_deleted_worktree_prunes_stale_metadata(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC-S6.5: hand-deleted worktree dir is cleaned by git worktree prune sweep."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "s6-hand-del"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id="t1",
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id="exec",
        message="hi",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint="fp",
        execution_id="exec",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id="t1",
            model_id="composer-2.5",
        ),
        source_repo=str(source_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="implement",
        worker_instance="worker-a",
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    shutil.rmtree(wt)
    before = subprocess.run(
        ["git", "-C", str(source_repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert str(wt.resolve()) in before.stdout
    sweep = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert sweep.reaped == 0
    assert sweep.stale_metadata_pruned
    after = subprocess.run(
        ["git", "-C", str(source_repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert str(wt.resolve()) not in after.stdout
