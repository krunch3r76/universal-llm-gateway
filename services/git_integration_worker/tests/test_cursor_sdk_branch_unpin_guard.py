"""Unpin live-bridge guard and scoped registry lookup (AC-A-2, AC-A-3, AC-A-4)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_branch_unpin import (
    unpin_registered_lane_worktree,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    reset_occupancy_cache,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    register_lane_worktree,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    CursorDispatchLedger._instance = None
    reset_occupancy_cache()
    yield
    CursorDispatchLedger._instance = None
    reset_occupancy_cache()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-a"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


@pytest.fixture
def git_repo_b(tmp_path: Path) -> Path:
    repo = tmp_path / "repo-b"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def test_ac_a_3_unpin_skips_live_bridge_and_emits(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-A-3: live bridge blocks unpin and emits reap_skipped stage=unpin."""
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-9831"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-9831"
    wt.mkdir()
    _git("worktree", "add", str(wt), branch, cwd=git_repo)
    register_lane_worktree(
        source_repo=git_repo,
        thread_id="9831",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    emitted: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "emit_sdk_lane_b_reap_skipped_live_bridge",
        lambda **kwargs: emitted.append(kwargs),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "worktree_held_by_live_bridge",
        lambda **kwargs: 4242,
    )
    result = unpin_registered_lane_worktree(repo=git_repo, branch_name=branch)
    assert result.unpinned is False
    assert emitted == [
        {
            "worktree_path": str(wt.resolve()),
            "pid": 4242,
            "dispatch_id": None,
            "stage": "unpin",
        }
    ]
    assert wt.is_dir()


def test_ac_a_4_read_only_dispatch_blocks_unpin_via_inheritor(
    git_repo: Path,
) -> None:
    """AC-A-4: read_only admitted dispatch counts as inheritor."""
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-ro"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-ro"
    wt.mkdir()
    register_lane_worktree(
        source_repo=git_repo,
        thread_id="ro-thread",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    ledger = CursorDispatchLedger.instance()
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    ledger.admit(
        req=CursorDispatchRequest(
            thread_id="ro-thread",
            model="cursor/composer-2.5",
            dispatch_id="ro-live-dispatch",
            execution_id="exec-ro",
            message="x",
            read_only=True,
        ),
        fingerprint="fp",
        execution_id="exec-ro",
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id="ro-live-dispatch",
            thread_id="ro-thread",
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
        lease_key=str(wt.resolve()),
        contract="consult",
        worker_instance="worker-a",
    )
    result = unpin_registered_lane_worktree(
        repo=git_repo,
        branch_name=branch,
        completing_dispatch_id="other-dispatch",
    )
    assert result.unpinned is False
    assert result.inherited is True


def test_ac_a_2_unpin_emits_worktree_removed(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-A-2: successful unpin emits sdk.lane_b.worktree_removed."""
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-emit"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-emit"
    wt.mkdir()
    _git("worktree", "add", str(wt), branch, cwd=git_repo)
    register_lane_worktree(
        source_repo=git_repo,
        thread_id="emit",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    removed: list[dict] = []
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "emit_sdk_lane_b_worktree_removed",
        lambda **kwargs: removed.append(kwargs),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_unpin."
        "worktree_held_by_live_bridge",
        lambda **kwargs: None,
    )
    result = unpin_registered_lane_worktree(repo=git_repo, branch_name=branch)
    assert result.unpinned is True
    assert removed and removed[0]["trigger"] == "unpin"
    assert removed[0]["source_repo"] == str(git_repo.resolve())
    assert not wt.is_dir()


def test_record_for_branch_scoped_by_source_repo(
    git_repo: Path,
    git_repo_b: Path,
) -> None:
    """AC-B-1 precursor: same branch name in two repos resolves independently."""
    tip_a = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    tip_b = subprocess.check_output(
        ["git", "-C", str(git_repo_b), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-shared"
    wt_a = git_repo.parent / "lane-a"
    wt_b = git_repo_b.parent / "lane-b"
    wt_a.mkdir()
    wt_b.mkdir()
    register_lane_worktree(
        source_repo=git_repo,
        thread_id="shared",
        worktree_path=wt_a,
        branch_name=branch,
        branch_point=tip_a,
    )
    register_lane_worktree(
        source_repo=git_repo_b,
        thread_id="shared",
        worktree_path=wt_b,
        branch_name=branch,
        branch_point=tip_b,
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_branch_unpin._remove_worktree",
        return_value=None,
    ) as remove_mock:
        with patch(
            "services.git_integration_worker.cursor_sdk_branch_unpin."
            "worktree_held_by_live_bridge",
            return_value=None,
        ):
            with patch(
                "services.git_integration_worker.cursor_sdk_branch_unpin."
                "_is_git_worktree",
                return_value=True,
            ):
                unpin_registered_lane_worktree(repo=git_repo, branch_name=branch)
    remove_mock.assert_called_once()
    assert remove_mock.call_args.kwargs["worktree_path"].resolve() == wt_a.resolve()
