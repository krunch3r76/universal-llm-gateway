"""Auto Lane-B episode discharge on terminal failures."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.git_integration_worker.cursor_auto.lane_b_episode_discharge import (
    maybe_discharge_failed_episode,
    same_thread_successor_exists,
)
from services.git_integration_worker.cursor_auto.queue import AutoJob
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    DISCHARGE_DISCARD,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    register_lane_worktree,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)
    yield
    CursorDispatchLedger._instance = None
    reset_queue_for_tests(durable=False)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source_repo"
    repo.mkdir()
    _git("init", "-b", "master", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-m", "seed", cwd=repo)
    return repo


def _job(*, thread_id: str = "9554") -> AutoJob:
    return AutoJob(
        job_id="j-fail",
        thread_id=thread_id,
        turn_number=1,
        subject="DIRECTIVE",
        body="TYPE: DIRECTIVE\n",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )


def test_same_thread_successor_exists_when_incumbent() -> None:
    from services.git_integration_worker.cursor_auto.queue import reset_queue_for_tests

    queue = reset_queue_for_tests(durable=False)
    old = queue.enqueue(
        thread_id="9554",
        turn_number=1,
        subject="old",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    queue.claim_next()
    new = queue.enqueue(
        thread_id="9554",
        turn_number=2,
        subject="new",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    assert same_thread_successor_exists(new) is True
    assert same_thread_successor_exists(old) is True


def test_terminal_failed_discharges_when_no_successor(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-9554"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-9554"
    wt.mkdir()
    register_lane_worktree(
        thread_id="9554",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(git_repo))
    result = maybe_discharge_failed_episode(
        _job(),
        dispatch_id="auto-deadbeef",
        summary="poll timeout",
    )
    assert result is not None
    assert result.discharged is True
    assert result.verb == DISCHARGE_DISCARD
    proc = subprocess.run(
        ["git", "-C", str(git_repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0


def test_terminal_failed_skips_discharge_when_successor(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-9554"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-9554"
    wt.mkdir()
    register_lane_worktree(
        thread_id="9554",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(git_repo))
    from services.git_integration_worker.cursor_auto.queue import get_queue

    queue = get_queue()
    failed = queue.enqueue(
        thread_id="9554",
        turn_number=1,
        subject="old",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    queue.claim_next()
    queue.enqueue(
        thread_id="9554",
        turn_number=2,
        subject="new",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    result = maybe_discharge_failed_episode(
        failed,
        dispatch_id="auto-deadbeef",
        summary="poll timeout",
    )
    assert result is None


def test_supersede_inherits_lane_tree(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Predecessor supersede must not discard the registered lane branch."""
    tip = subprocess.check_output(
        ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    branch = "cursor-sdk/lane-9554"
    _git("branch", branch, tip, cwd=git_repo)
    wt = git_repo.parent / "lane-9554"
    wt.mkdir()
    register_lane_worktree(
        thread_id="9554",
        worktree_path=wt,
        branch_name=branch,
        branch_point=tip,
    )
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(git_repo))
    from services.git_integration_worker.cursor_auto.queue import get_queue

    queue = get_queue()
    old = queue.enqueue(
        thread_id="9554",
        turn_number=1,
        subject="old",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    queue.claim_next()
    queue.enqueue(
        thread_id="9554",
        turn_number=2,
        subject="new",
        body="body",
        from_agent="web-anthropic",
        to_agent="cursor",
        desired_model="auto",
        desired_effort="medium",
        contract="implement",
    )
    with patch(
        "services.git_integration_worker.cursor_auto.lane_b_episode_discharge.discharge"
    ) as discharge_mock:
        result = maybe_discharge_failed_episode(
            old,
            dispatch_id="auto-old",
            summary="superseded",
        )
    assert result is None
    discharge_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handler_terminal_failed_observes_discharge(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.git_integration_worker.cursor_auto.handler_terminal import (
        terminal_failed,
    )

    discharge = MagicMock(
        discharged=True,
        branch="cursor-sdk/lane-9554",
        verb=DISCHARGE_DISCARD,
        refused_reason=None,
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_auto.lane_b_episode_discharge.maybe_discharge_failed_episode",
        MagicMock(return_value=discharge),
    )
    bus = AsyncMock()
    bus.reply = AsyncMock(return_value=MagicMock(status_code=200, body={}))
    queue = MagicMock()
    queue.mark_done = MagicMock()
    job = _job()
    result = await terminal_failed(
        job,
        client=bus,
        queue=queue,
        summary="nested dispatch submit failed",
        extra={"error": "x"},
        dispatch_id="auto-deadbeef",
    )
    assert result["terminal_status"] == "status:failed"
    assert result["ok"] is False
