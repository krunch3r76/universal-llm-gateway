"""Unit tests for integrate_op orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from git_integrate.git_cas import diff_sha256
from git_integrate.integrate import integrate_op
from git_integrate.schema import (
    RC_ARC_BRANCH_MISMATCH,
    RC_CAS_EXHAUSTED,
    RC_DIFF_MISMATCH,
    RC_GATE_FAILED,
    RC_INTEGRATE_CONFLICT,
    RC_WORKTREE_MISSING,
)


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _ref_sha(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _passing_gate() -> list[str]:
    return ["true"]


def _failing_gate() -> list[str]:
    return ["false"]


@pytest.mark.asyncio
async def test_diff_mismatch_rejects(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256="wrong" * 16,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_DIFF_MISMATCH
    signals = [s for s, _ in event_log]
    assert "git.integrate.rejected" in signals
    assert "git.integrate.requested" not in signals


@pytest.mark.asyncio
async def test_wrong_branch_rejects(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    out = await integrate_op(
        arc="wrong-arc-name",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=diff_sha256(str(arc_worktree)),
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_ARC_BRANCH_MISMATCH


@pytest.mark.asyncio
async def test_missing_worktree_rejects(
    source_repo: Path,
    event_log: list[tuple[str, dict[str, Any]]],
    tmp_path: Path,
) -> None:
    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(tmp_path / "nonexistent"),
        approval="approved",
        expected_diff_sha256="x" * 64,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_WORKTREE_MISSING


@pytest.mark.asyncio
async def test_gate_failed_leaves_arc_at_pretip(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    arc_tip_before = _head_sha(arc_worktree)
    sha = diff_sha256(str(arc_worktree))

    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_failing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_GATE_FAILED
    assert out["gate_exit"] == 1

    # master must NOT have advanced
    assert _ref_sha(source_repo, "refs/heads/master") != _head_sha(arc_worktree)

    # arc branch must be back at the pre-merge tip
    assert _head_sha(arc_worktree) == arc_tip_before

    signals = [s for s, _ in event_log]
    assert "git.integrate.gate.failed" in signals


@pytest.mark.asyncio
async def test_conflict_aborts_and_rejects(
    tmp_path: Path,
    source_repo: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    wt = tmp_path / "worktrees" / "conflict-arc2"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git(
        "worktree", "add", "-b", "arc/conflict-arc2", str(wt), "master", cwd=source_repo
    )
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)

    # arc modifies README
    (wt / "README.md").write_text("arc side\n")
    _git("add", "README.md", cwd=wt)
    _git("commit", "-m", "arc side", cwd=wt)

    sha = diff_sha256(str(wt))

    # master also modifies README
    _git("checkout", "master", cwd=source_repo)
    (source_repo / "README.md").write_text("master side\n")
    _git("add", "README.md", cwd=source_repo)
    _git("commit", "-m", "master side", cwd=source_repo)
    _git("checkout", "_integration_parked", cwd=source_repo)

    out = await integrate_op(
        arc="conflict-arc2",
        phase="phase-3",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_INTEGRATE_CONFLICT

    # worktree must be clean after abort
    status = subprocess.run(
        ["git", "-C", str(wt), "status", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert status == ""


@pytest.mark.asyncio
async def test_cas_retry_then_success(
    tmp_path: Path,
    source_repo: Path,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CAS non_ff on first attempt → retry → succeeds on second."""
    wt = tmp_path / "worktrees" / "retry-arc"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", "arc/retry-arc", str(wt), "master", cwd=source_repo)
    _git("config", "user.email", "test@example.com", cwd=wt)
    _git("config", "user.name", "Test", cwd=wt)
    (wt / "retry.py").write_text("# retry\n")
    _git("add", "retry.py", cwd=wt)
    _git("commit", "-m", "retry feature", cwd=wt)

    sha = diff_sha256(str(wt))

    call_count = 0
    original_advance = __import__(
        "git_integrate.git_cas", fromlist=["advance_master_cas"]
    ).advance_master_cas

    async def _patched_advance(src: str, wtp: str, *, expected: str) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            from git_integrate.schema import CasResult

            return CasResult(non_ff=True)
        return await original_advance(src, wtp, expected=expected)

    monkeypatch.setattr("git_integrate.ops_common.advance_master_cas", _patched_advance)

    out = await integrate_op(
        arc="retry-arc",
        phase="phase-3",
        worktree_path=str(wt),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
    )

    assert out["status"] == "completed", out
    assert call_count == 2
    signals = [s for s, _ in event_log]
    assert "git.integrate.retried" in signals
    assert "git.integrate.completed" in signals


@pytest.mark.asyncio
async def test_cas_exhausted_after_max_attempts(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = diff_sha256(str(arc_worktree))

    async def _always_non_ff(src: str, wtp: str, *, expected: str) -> Any:
        from git_integrate.schema import CasResult

        return CasResult(non_ff=True)

    monkeypatch.setattr("git_integrate.ops_common.advance_master_cas", _always_non_ff)

    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
        max_attempts=3,
    )

    assert out["status"] == "rejected"
    assert out["reason_code"] == RC_CAS_EXHAUSTED
    assert out["attempts"] == 3

    signals = [s for s, _ in event_log]
    assert signals.count("git.integrate.retried") == 3


@pytest.mark.asyncio
async def test_success_emits_completed_and_advances_master(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    sha = diff_sha256(str(arc_worktree))
    master_before = _ref_sha(source_repo, "refs/heads/master")

    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=str(arc_worktree),
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
        remove_worktree=False,
    )

    assert out["status"] == "completed", out
    assert out["master_sha"] != master_before
    assert out["merge_commit"]

    master_after = _ref_sha(source_repo, "refs/heads/master")
    assert master_after == out["master_sha"]

    signals = [s for s, _ in event_log]
    assert "git.integrate.requested" in signals
    assert "git.integrate.completed" in signals


@pytest.mark.asyncio
async def test_success_teardown_removes_worktree(
    source_repo: Path,
    arc_worktree: Path,
    event_log: list[tuple[str, dict[str, Any]]],
) -> None:
    sha = diff_sha256(str(arc_worktree))
    worktree_path = str(arc_worktree)

    out = await integrate_op(
        arc="test-arc",
        phase="phase-3",
        worktree_path=worktree_path,
        approval="approved",
        expected_diff_sha256=sha,
        source_repo=str(source_repo),
        green_gate_cmd=_passing_gate(),
        remove_worktree=True,
    )

    assert out["status"] == "completed"
    assert not arc_worktree.exists()
