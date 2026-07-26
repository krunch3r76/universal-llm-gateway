"""Bound-invariant falsifiers — S1b worktree mint/GC (F-A4, F-A5, AC1)."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_worktree import (
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    reap_orphan_worktrees,
    resolve_admit_binding,
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
    assert maybe_prune_worktree_on_terminal(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert not wt.exists()

    orphan_id = "fa4-orphan"
    orphan_wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=orphan_id,
    )
    assert orphan_wt.is_dir()
    assert reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    ) >= 1
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
    )
    assert workspace != shared
    assert workspace.is_dir()
    assert str(workspace.resolve()) == lease_key
    assert workspace.relative_to(worktree_root.resolve())
