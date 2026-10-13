"""Hermetic belt: reap entrypoints log caller identity and refuse the live hub under pytest."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from services.git_integration_worker.config import _DEFAULT_SOURCE_REPO
from services.git_integration_worker.cursor_sdk_branch_discharge import (
    _delete_branch,
)
from services.git_integration_worker.cursor_sdk_lane_b_disposition import (
    mark_lane_b_disposition,
)
from services.git_integration_worker.cursor_sdk_worktree import reap_orphan_worktrees
from services.git_integration_worker.cursor_sdk_worktree_gc import (
    _delete_orphan_branch,
    _gc_delete_candidates,
    gc_merged_dispatch_branches,
)
from services.git_integration_worker.cursor_sdk_worktree_release import (
    _git_worktree_remove,
)
from services.git_integration_worker.cursor_sdk_reap_pytest_guard import (
    refuse_real_hub_reap_under_pytest,
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
    assert success[-1]["outcome"] == "deleted"
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
    assert failed[-1]["outcome"] == "failed"
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
    assert reaped[-1]["outcome"] == "failed"
    assert "worktree_remove_failed" in str(reaped[-1].get("reason", ""))


def test_discharge_delete_branch_emits_reaped_on_git_failure(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discharge ``git branch -D`` failure emits ``sdk.lane_b.reaped`` with stderr."""
    branch = "cursor-sdk/lane-discharge-fail"
    _git("checkout", "-b", branch, cwd=git_repo)
    (git_repo / "z.py").write_text("z = 1\n", encoding="utf-8")
    _git("add", "z.py", cwd=git_repo)
    _git("commit", "-m", "tip", cwd=git_repo)
    _git("checkout", "master", cwd=git_repo)

    real_run = subprocess.run

    def _fail_delete(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if isinstance(cmd, list) and "-D" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                returncode=1,
                stdout="",
                stderr="discharge delete blocked",
            )
        return real_run(cmd, **kwargs)  # type: ignore[arg-type]

    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        with patch(
            "services.git_integration_worker.cursor_sdk_branch_discharge.subprocess.run",
            side_effect=_fail_delete,
        ):
            ok, err = _delete_branch(git_repo, branch)
    assert not ok
    assert "discharge delete blocked" in (err or "")
    reaped = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert reaped
    assert reaped[-1]["outcome"] == "failed"
    assert reaped[-1]["branch"] == branch


def test_reaped_outcome_distinguishes_failed_delete_from_kept_branch(
    git_repo: Path,
) -> None:
    """``outcome`` field separates a failed delete from a prune that kept the branch."""
    branch = "cursor-sdk/lane-kept"
    _git("checkout", "-b", branch, cwd=git_repo)
    (git_repo / "keep.py").write_text("k = 1\n", encoding="utf-8")
    _git("add", "keep.py", cwd=git_repo)
    _git("commit", "-m", "ahead", cwd=git_repo)
    _git("checkout", "master", cwd=git_repo)

    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        from services.git_integration_worker.cursor_sdk_worktree_prune import (
            emit_sdk_lane_b_reaped,
        )

        emit_sdk_lane_b_reaped(
            dispatch_id="d-kept",
            branch_deleted=False,
            outcome="kept",
            branch=None,
            tip_sha=None,
            reason=None,
        )
        real_run = subprocess.run

        def _fail_delete(
            cmd: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if isinstance(cmd, list) and "-D" in cmd:
                return subprocess.CompletedProcess(
                    cmd,
                    returncode=1,
                    stdout="",
                    stderr="outcome probe failure",
                )
            return real_run(cmd, **kwargs)  # type: ignore[arg-type]

        with patch(
            "services.git_integration_worker.cursor_sdk_worktree_gc.subprocess.run",
            side_effect=_fail_delete,
        ):
            _delete_orphan_branch(
                repo=git_repo,
                branch_name=branch,
                reason="mechanical_safe",
                dispatch_id="d-fail",
            )

    reaped = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    kept = [e for e in reaped if e.get("outcome") == "kept"]
    failed = [e for e in reaped if e.get("outcome") == "failed"]
    assert kept and failed
    assert kept[0]["outcome"] == "kept"
    assert failed[-1]["outcome"] == "failed"
    assert failed[-1]["branch"] == branch


def test_pytest_hub_refusal_emits_reap_refused_pytest_event() -> None:
    """Guard trip emits ``sdk.lane_b.reap_refused_pytest`` before raising."""
    hub = Path(_DEFAULT_SOURCE_REPO)
    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        with pytest.raises(RuntimeError, match="live hub repo"):
            refuse_real_hub_reap_under_pytest(source_repo=hub)
    refused = [e for e in emitted if e.get("signal") == "sdk.lane_b.reap_refused_pytest"]
    assert len(refused) == 1
    assert refused[0]["source_repo"] == str(hub.resolve())


def test_gc_respects_retained_disposition_set_during_archive_pass(
    git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A branch marked ``retained_for_mission`` mid-pass is not deleted."""
    first = "cursor-sdk/lane-gc-first"
    second = "cursor-sdk/lane-gc-second"
    for branch in (first, second):
        _git("checkout", "-b", branch, cwd=git_repo)
        (git_repo / f"{branch.split('/')[-1]}.py").write_text("x\n", encoding="utf-8")
        _git("add", ".", cwd=git_repo)
        _git("commit", "-m", "tip", cwd=git_repo)
        _git("checkout", "master", cwd=git_repo)
        _git("merge", branch, cwd=git_repo)

    from services.git_integration_worker.cursor_sdk_branch_archive import (
        archive_branch as real_archive_branch,
    )

    def _archive_with_retain(*, repo: Path, branch_name: str) -> str | None:
        if branch_name == first:
            mark_lane_b_disposition(
                branch_name=second,
                reason="retained_for_mission",
                dispatch_id="mid-pass",
            )
        return real_archive_branch(repo=repo, branch_name=branch_name)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_branch_archive.archive_branch",
        _archive_with_retain,
    )
    deleted = gc_merged_dispatch_branches(source_repo=git_repo)
    assert deleted >= 1
    listing = subprocess.run(
        ["git", "branch", "--list", second],
        cwd=str(git_repo),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert second in listing


def test_reap_orphan_worktrees_scans_gc_candidates_once(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One reap tick calls ``_gc_delete_candidates`` exactly once."""
    worktree_root = tmp_path / "wt"
    worktree_root.mkdir()
    scan_calls = 0
    real_scan = _gc_delete_candidates

    def _counting_scan(*, source_repo: Path) -> list[tuple[str, str, str | None, str | None]]:
        nonlocal scan_calls
        scan_calls += 1
        return real_scan(source_repo=source_repo)

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_worktree_gc._gc_delete_candidates",
        _counting_scan,
    )
    reap_orphan_worktrees(source_repo=git_repo, worktree_root=worktree_root)
    assert scan_calls == 1


def test_prune_terminal_emits_one_reaped_when_branch_deleted(
    git_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful prune delete emits exactly one ``sdk.lane_b.reaped`` (``deleted``)."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_worktree import (
        mint_dispatch_worktree,
    )
    from services.git_integration_worker.cursor_sdk_worktree_prune import (
        prune_dispatch_worktree,
    )

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    worktree_root = tmp_path / "worktrees"
    dispatch_id = "one-reaped"
    mint_dispatch_worktree(
        source_repo=git_repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
    )
    ledger = CursorDispatchLedger.instance()
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )

    req = CursorDispatchRequest(
        thread_id=dispatch_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="x",
        worktree_isolated=True,
    )
    ledger.admit(
        req=req,
        fingerprint="fp",
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=dispatch_id,
            model_id="composer-2.5",
        ),
        source_repo=str(git_repo.resolve()),
    )
    ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status="completed")

    emitted: list[dict] = []

    def _capture(signal: str, **payload: object) -> None:
        emitted.append({"signal": signal, **payload})

    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_orphan.live_bridge_occupancy",
        lambda *_a, **_k: [],
    )
    with patch(
        "services.git_integration_worker.cursor_sdk_events.record",
        side_effect=_capture,
    ):
        result = prune_dispatch_worktree(
            dispatch_id=dispatch_id,
            source_repo=git_repo,
        )
    assert result.pruned
    reaped = [e for e in emitted if e.get("signal") == "sdk.lane_b.reaped"]
    assert len(reaped) == 1
    assert reaped[0]["outcome"] == "deleted"
