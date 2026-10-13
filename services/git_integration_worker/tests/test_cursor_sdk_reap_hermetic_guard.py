"""Hermetic belt: reap entrypoints log caller identity and refuse the live hub under pytest."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.config import _DEFAULT_SOURCE_REPO
from services.git_integration_worker.cursor_sdk_worktree_gc import (
    _delete_orphan_branch,
    gc_merged_dispatch_branches,
)
from services.git_integration_worker.cursor_sdk_worktree_release import (
    _git_worktree_remove,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "t@example.com", cwd=root)
    _git("config", "user.name", "t", cwd=root)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    return root


def test_pytest_refuses_live_hub_repo_path(git_repo: Path) -> None:
    """(a) Hub-equal ``source_repo`` raises under pytest; tmp repos do not."""
    hub = Path(_DEFAULT_SOURCE_REPO)
    with pytest.raises(RuntimeError, match="live hub repo"):
        gc_merged_dispatch_branches(source_repo=hub)

    gc_merged_dispatch_branches(source_repo=git_repo)


def test_reap_pass_logs_caller_pid_and_argv(
    git_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """(b) A reap pass emits one log line with pid, argv slice, repo, and action count."""
    caplog.set_level("INFO")
    gc_merged_dispatch_branches(source_repo=git_repo)
    lines = [
        r.message
        for r in caplog.records
        if "lane_b reap pass" in r.message
        and "gc_merged_dispatch_branches" in r.message
    ]
    assert len(lines) == 1
    line = lines[0]
    assert f"pid={os.getpid()}" in line
    assert "argv0=" in line
    assert f"repo={git_repo.resolve()}" in line
    assert "action_count=" in line


def test_branch_delete_emits_reaped_on_success_and_on_git_failure(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) ``git branch -D`` success and failure both surface via ``sdk.lane_b.reaped``."""
    branch = "cursor-sdk/lane-test-delete"
    _git("checkout", "-b", branch, cwd=git_repo)
    (git_repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "x.py", cwd=git_repo)
    _git("commit", "-m", "tip", cwd=git_repo)
    _git("checkout", "master", cwd=git_repo)
    _git("merge", branch, cwd=git_repo)

    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        ok = _delete_orphan_branch(
            repo=git_repo,
            branch_name=branch,
            reason="ancestry_merged",
            dispatch_id="d-ok",
        )
    assert ok
    success = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert success[-1]["branch_deleted"] is True
    assert success[-1]["branch"] == branch

    branch2 = "cursor-sdk/lane-fail-delete"
    _git("checkout", "-b", branch2, cwd=git_repo)
    (git_repo / "y.py").write_text("y = 1\n", encoding="utf-8")
    _git("add", "y.py", cwd=git_repo)
    _git("commit", "-m", "tip2", cwd=git_repo)
    _git("checkout", "master", cwd=git_repo)
    _git("merge", branch2, cwd=git_repo)

    real_run = subprocess.run

    def _fail_delete(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, list) and "-D" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout="",
                stderr="simulated delete refusal",
            )
        return real_run(cmd, **kwargs)  # type: ignore[arg-type]

    emitted.clear()
    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_worktree_gc.subprocess.run",
            side_effect=_fail_delete,
        ):
            bad = _delete_orphan_branch(
                repo=git_repo,
                branch_name=branch2,
                reason="ancestry_merged",
                dispatch_id="d-fail",
            )
    assert not bad
    failed = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert failed
    assert failed[-1]["branch_deleted"] is False
    assert "simulated delete refusal" in str(failed[-1].get("reason", ""))


def test_worktree_remove_emits_reaped_on_failure(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) Failed ``git worktree remove`` emits ``sdk.lane_b.reaped`` with stderr."""
    wt = tmp_path / "lane-missing"
    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        ok = _git_worktree_remove(
            git_repo,
            wt,
            dispatch_id="d-wt",
            branch="cursor-sdk/lane-x",
            reason="reap",
        )
    assert not ok
    reaped = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert reaped
    assert reaped[-1]["branch_deleted"] is False
    assert "worktree_remove_failed" in str(reaped[-1].get("reason", ""))
