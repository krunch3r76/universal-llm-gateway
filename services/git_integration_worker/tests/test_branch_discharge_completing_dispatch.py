"""The landing dispatch is not its own inheritor (a:36643)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    discharge_discard,
    resolve_completing_dispatch_id,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    reset_occupancy_cache,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    register_lane_worktree,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CURSOR_SDK_DISPATCH_ID", raising=False)
    CursorDispatchLedger._instance = None
    reset_occupancy_cache()
    reset_queue_for_tests(durable=False)
    yield
    CursorDispatchLedger._instance = None
    reset_occupancy_cache()
    reset_queue_for_tests(durable=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    return root


def _branch(repo: Path, name: str) -> None:
    _git("checkout", "-b", name, cwd=repo)
    (repo / "note.txt").write_text(name + "\n", encoding="utf-8")
    _git("add", "note.txt", cwd=repo)
    _git("commit", "-m", name, cwd=repo)
    _git("checkout", "master", cwd=repo)


def _running(dispatch_id: str, thread_id: str, *, repo: Path, tree: Path) -> None:
    ledger = CursorDispatchLedger.instance()
    ledger.admit(
        req=CursorDispatchRequest(
            thread_id=thread_id,
            model="cursor/composer-2.5",
            dispatch_id=dispatch_id,
            execution_id=f"exec-{dispatch_id}",
            message="x",
            read_only=True,
        ),
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
        source_repo=str(repo.resolve()),
        lease_key=f"{tree.resolve()}:{dispatch_id}",
        contract="consult",
        worker_instance="worker-a",
        read_only=True,
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='running' WHERE dispatch_id=?",
            (dispatch_id,),
        )


def test_caller_running_alone_discharges(repo: Path, tmp_path: Path) -> None:
    branch = "cursor-sdk/lane-caller"
    _branch(repo, branch)
    tree = tmp_path / "lane-caller"
    _git("worktree", "add", str(tree), branch, cwd=repo)
    register_lane_worktree(
        source_repo=repo,
        thread_id="thr-caller",
        worktree_path=tree,
        branch_name=branch,
        branch_point="master",
        last_dispatch_id="disp-caller",
    )
    _running("disp-caller", "thr-caller", repo=repo, tree=tree)
    result = discharge_discard(
        repo=repo,
        branch_name=branch,
        reason="caller finished",
        completing_dispatch_id="disp-caller",
    )
    assert result.discharged is True
    assert result.inherited is False


def test_second_live_dispatch_still_inherited(repo: Path, tmp_path: Path) -> None:
    branch = "cursor-sdk/lane-two"
    _branch(repo, branch)
    tree = tmp_path / "lane-two"
    _git("worktree", "add", str(tree), branch, cwd=repo)
    register_lane_worktree(
        source_repo=repo,
        thread_id="thr-two",
        worktree_path=tree,
        branch_name=branch,
        branch_point="master",
        last_dispatch_id="disp-caller",
    )
    _running("disp-caller", "thr-two", repo=repo, tree=tree)
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute(
            "INSERT INTO cursor_sdk_dispatches "
            "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, status) "
            "VALUES (?, ?, ?, ?, ?, 'running')",
            (
                "disp-sibling",
                "fp-sibling",
                "thr-two",
                "exec-disp-sibling",
                "composer-2.5",
            ),
        )
    result = discharge_discard(
        repo=repo,
        branch_name=branch,
        reason="should inherit",
        completing_dispatch_id="disp-caller",
    )
    assert result.discharged is False
    assert result.inherited is True
    assert result.refused_reason == "inherited by successor"
    assert tree.exists()


def test_harness_env_supplies_completing_dispatch_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_ID", "disp-from-env")
    assert resolve_completing_dispatch_id(None) == "disp-from-env"
    assert resolve_completing_dispatch_id("disp-body") == "disp-body"
