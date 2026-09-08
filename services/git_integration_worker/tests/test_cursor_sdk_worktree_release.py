"""Tests for Lane-B worktree release chokepoint (S1 Leg C)."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.cursor_sdk_worktree_lock import lock_lane_worktree
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    ensure_worktree_schema,
    pin_lane_worktree,
    register_lane_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
from services.git_integration_worker.cursor_sdk_worktree_release import (
    ReleaseRefusal,
    release_lane_worktree,
    reset_unharvested_emit_dedupe,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    prune_dispatch_worktree,
    rollback_dispatch_worktree,
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
def _ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    CursorDispatchLedger.instance()
    reset_unharvested_emit_dedupe()
    yield
    CursorDispatchLedger._instance = None
    reset_unharvested_emit_dedupe()


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "f").write_text("x\n")
    _git("add", "f", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


def _pin_tree(
    *,
    repo: Path,
    wt: Path,
    thread_id: str,
    dispatch_id: str,
) -> None:
    lock_reason = lock_lane_worktree(
        repo, wt, dispatch_id=dispatch_id, thread_id=thread_id
    )
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        pin_lane_worktree(
            conn,
            source_repo=repo,
            thread_id=thread_id,
            dispatch_id=dispatch_id,
            worktree_path=wt,
            lock_reason=lock_reason,
        )


from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _admit(
    *,
    ledger: CursorDispatchLedger,
    dispatch_id: str,
    thread_id: str,
    source_repo: Path,
    lease_key: str,
) -> None:
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="work",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint=f"fp-{dispatch_id}",
        execution_id=f"exec-{dispatch_id}",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
        lease_key=lease_key,
        source_repo=str(source_repo.resolve()),
    )


def test_ac_s1_9_chokepoint_no_worktree_remove_outside_release() -> None:
    """AC-S1-9: only ``cursor_sdk_worktree_release`` may call git worktree remove."""
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if path.name.startswith("test_"):
            continue
        if path.name == "cursor_sdk_worktree_release.py":
            continue
        rel = path.relative_to(root)
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "run":
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "subprocess":
                continue
            args: list[str] = []
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.List):
                    for elt in first.elts:
                        if isinstance(elt, ast.Constant) and isinstance(
                            elt.value, str
                        ):
                            args.append(elt.value)
            if "worktree" in args and "remove" in args:
                offenders.append(str(rel))
    assert offenders == []


def test_release_refuses_unharvested_with_active_pin(
    source_repo: Path, tmp_path: Path
) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "disp-unharvested"
    thread_id = "thread-unharvested"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    _pin_tree(
        repo=source_repo,
        wt=wt,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")

    result = release_lane_worktree(
        source_repo=source_repo,
        dispatch_id=dispatch_id,
        reason="reap",
    )
    assert not result.released
    assert result.refusal == ReleaseRefusal.UNHARVESTED
    assert wt.is_dir()


def test_rollback_allow_unharvested_removes_pinned_tree(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "rollback-pin"
    thread_id = "t-rollback-pin"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    _pin_tree(
        repo=source_repo,
        wt=wt,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    result = rollback_dispatch_worktree(
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
    )
    assert result.pruned
    assert not wt.exists()


def test_release_by_dispatch_id_stale_last_dispatch(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S1-13: resolver ladder reaches row when last_dispatch_id is stale."""
    worktree_root = tmp_path / "worktrees"
    thread_id = "thread-stale"
    old_dispatch = "old-dispatch"
    new_dispatch = "new-dispatch"
    wt = tmp_path / "lane-stale"
    tip = _git("rev-parse", "HEAD", cwd=source_repo).stdout.strip()
    branch = f"cursor-sdk/lane-{thread_id}"
    _git("branch", branch, tip, cwd=source_repo)
    wt.mkdir()
    _git("worktree", "add", str(wt), branch, cwd=source_repo)
    register_lane_worktree(
        source_repo=source_repo,
        thread_id=thread_id,
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
        last_dispatch_id=old_dispatch,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    _admit(
        ledger=CursorDispatchLedger.instance(),
        dispatch_id=new_dispatch,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    CursorDispatchLedger.instance().mark_terminal(
        dispatch_id=new_dispatch,
        terminal_status="completed",
    )
    result = release_lane_worktree(
        source_repo=source_repo,
        dispatch_id=new_dispatch,
        reason="reap",
        allow_unharvested=True,
    )
    assert result.released
    assert not wt.is_dir()


def test_operator_release_waives_unharvested(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S1-12: operator release succeeds on terminal unharvested lane."""
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "op-release"
    thread_id = "t-op"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    _pin_tree(
        repo=source_repo,
        wt=wt,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    _admit(
        ledger=ledger,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        source_repo=source_repo,
        lease_key=str(wt.resolve()),
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    result = release_lane_worktree(
        source_repo=source_repo,
        thread_id=thread_id,
        dispatch_id=dispatch_id,
        allow_unharvested=True,
        reason="operator_release:lead",
        actor="lead",
    )
    assert result.released
    assert not wt.is_dir()


def test_prune_without_pin_still_releases(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "prune-no-pin"
    wt = mint_dispatch_worktree(
        source_repo=source_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_live_guard."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    result = prune_dispatch_worktree(
        dispatch_id=dispatch_id,
        source_repo=source_repo,
        remove_trigger="reap",
    )
    assert result.pruned
    assert not wt.exists()
