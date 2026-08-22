"""S1b worktree mint, prune-on-terminal, and orphan reaper."""

from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    _connect,
)
from services.git_integration_worker.cursor_sdk_worktree import (
    WorktreeMintError,
    maybe_prune_worktree_on_terminal,
    mint_dispatch_worktree,
    reap_orphan_worktrees,
    resolve_admit_binding,
    resolve_master_branch_point,
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


def _req(**overrides: object) -> CursorDispatchRequest:
    base = {
        "thread_id": "t1",
        "model": "cursor/composer-2.5",
        "dispatch_id": "disp-1",
        "execution_id": "exec-disp-1",
        "message": "hello",
    }
    base.update(overrides)
    return CursorDispatchRequest(**base)


def test_s1b_mint_pins_resolved_commit(source_repo: Path, tmp_path: Path) -> None:
    """Mint uses an explicitly resolved branch point, not implicit tip sampling."""
    worktree_root = tmp_path / "worktrees"
    tip = resolve_master_branch_point(source_repo)
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="mint-a",
        branch_point=tip,
    )
    head = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    assert head == tip
    assert wt.is_dir()
    assert wt.parent == worktree_root.resolve()


def test_s1b_lane_b_resolve_admit_binding_mints(
    source_repo: Path, tmp_path: Path
) -> None:
    """AC1: Lane-B admit binding mints under worktree_root."""
    worktree_root = tmp_path / "worktrees"
    req = _req(dispatch_id="lane-b-1", worktree_isolated=True)
    workspace, lease_key = resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert workspace.is_dir()
    assert str(workspace.resolve()) == lease_key
    assert workspace.relative_to(worktree_root.resolve())


def test_s1b_prune_on_terminal(source_repo: Path, tmp_path: Path) -> None:
    """Lane trees outlive dispatches: terminal does not prune the worktree."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "prune-me"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    assert wt.is_dir()
    result = maybe_prune_worktree_on_terminal(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert not result.pruned
    assert wt.is_dir()


def test_s1b_prune_retains_branch_when_dirty(source_repo: Path, tmp_path: Path) -> None:
    """S3: explicit dirty prune retains the unmerged lane branch."""
    from services.git_integration_worker.cursor_sdk_worktree_prune import (
        prune_dispatch_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "prune-dirty"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    (wt / "dirty.py").write_text("x\n", encoding="utf-8")
    branch = f"cursor-sdk/lane-{dispatch_id}"
    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert result.branch_retained
    assert result.salvaged
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout


def test_s1b_reaper_retains_standing_lane_after_terminal(
    source_repo: Path, tmp_path: Path
) -> None:
    """Registered empty lane is idle after terminal, not an orphan."""
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_lane_worktree,
    )

    worktree_root = tmp_path / "worktrees"
    dispatch_id = "orphan-a"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    branch = f"cursor-sdk/lane-{dispatch_id}"
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=_req(dispatch_id=dispatch_id),
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
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    assert wt.is_dir()
    removed = reap_orphan_worktrees(
        source_repo=source_repo,
        worktree_root=worktree_root,
    )
    assert removed.reaped == 0
    assert wt.is_dir()
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout
    assert lookup_lane_worktree(thread_id=dispatch_id) is not None


def test_s1b_route_wires_resolve_admit_binding() -> None:
    """AC1: cursor_sdk route resolves Lane-B workspace before ledger admit."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "resolve_admit_binding" in source


def test_s1b_mint_reattaches_existing_lane_branch(
    source_repo: Path, tmp_path: Path
) -> None:
    """Branch exists and worktree dir is gone → attach without ``-b`` (7240 class)."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "7240-sim"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="reattach-1",
        thread_id=thread_id,
    )
    (wt / "lane_work.py").write_text("kept\n", encoding="utf-8")
    _git("add", "lane_work.py", cwd=wt)
    _git("commit", "-m", "lane work", cwd=wt)
    tip = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    branch = f"cursor-sdk/lane-{thread_id}"
    _git("worktree", "remove", "--force", str(wt), cwd=source_repo)
    assert not wt.exists()
    assert branch in _git("branch", "--list", branch, cwd=source_repo).stdout

    wt2 = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id="reattach-2",
        thread_id=thread_id,
    )
    assert wt2.is_dir()
    assert (wt2 / "lane_work.py").read_text(encoding="utf-8") == "kept\n"
    assert _git("rev-parse", "HEAD", cwd=wt2).stdout.strip() == tip
    assert _git("branch", "--show-current", cwd=wt2).stdout.strip() == branch


def test_s1b_admit_reattaches_when_registry_dir_gone(
    source_repo: Path, tmp_path: Path
) -> None:
    """Registry row pointing at a missing dir still remints by attaching the branch."""
    worktree_root = tmp_path / "worktrees"
    first, _key = resolve_admit_binding(
        req=_req(dispatch_id="gone-dir-1", thread_id="t-gone", worktree_isolated=True),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    (first / "kept.txt").write_text("visible\n", encoding="utf-8")
    _git("add", "kept.txt", cwd=first)
    _git("commit", "-m", "keep", cwd=first)
    _git("worktree", "remove", "--force", str(first), cwd=source_repo)
    assert not first.exists()

    second, key2 = resolve_admit_binding(
        req=_req(dispatch_id="gone-dir-2", thread_id="t-gone", worktree_isolated=True),
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=worktree_root,
        dispatch_workspace_default=source_repo.parent,
        lane="B",
    )
    assert second.is_dir()
    assert (second / "kept.txt").read_text(encoding="utf-8") == "visible\n"
    assert str(second.resolve()) == key2


def test_worktree_mint_error_defaults_not_retryable() -> None:
    """Permanent mint collisions are not retryable; lock exhaustion is."""
    permanent = WorktreeMintError("fatal: a branch named 'x' already exists")
    assert permanent.retryable is False
    transient = WorktreeMintError("index.lock: File exists", retryable=True)
    assert transient.retryable is True


def test_s1b_route_mint_failure_uses_exc_retryable() -> None:
    """Route must not hardcode retryable=True on every WorktreeMintError."""
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    source = Path(route_mod.__file__).read_text(encoding="utf-8")
    assert "retryable=getattr(exc, \"retryable\", False)" in source
    assert "except WorktreeMintError as exc:" in source


def test_s1b_lane_a_binding_unchanged(source_repo: Path, tmp_path: Path) -> None:
    """Lane-A default path still uses shared dispatch_workspace + source_repo lease."""
    shared = tmp_path / "shared"
    shared.mkdir()
    req = _req(worktree_isolated=False)
    workspace, lease_key = resolve_admit_binding(
        req=req,
        source_repo=source_repo,
        hub=source_repo,
        worktree_root=tmp_path / "worktrees",
        dispatch_workspace_default=shared,
        lane="A",
    )
    assert workspace == shared
    assert lease_key == str(source_repo.resolve())
