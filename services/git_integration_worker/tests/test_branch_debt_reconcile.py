"""Open debts whose branch ref vanished are graded by surviving tip evidence.

The sweeps this module backstops all asked "which branches need discharging".
None asked "which debts still have a branch", so a branch deleted out of band
left a row that outlived every pass. These cover the four gradings and, most
importantly, that an unverifiable tip is escalated rather than called landed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_branch_archive import archive_branch
from services.git_integration_worker.cursor_sdk_branch_debt import (
    get_branch_debt,
    open_branch_debt,
)
from services.git_integration_worker.cursor_sdk_branch_debt_reconcile import (
    _LIVE_GRADE_CAP,
    VERDICT_ANCESTOR,
    VERDICT_ARCHIVED,
    VERDICT_EQUIVALENT,
    VERDICT_INDETERMINATE,
    VERDICT_LIVE,
    VERDICT_RECOVERED,
    reconcile_open_branch_debts,
    reset_live_grade_memo_for_tests,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    containing_worktree_under_root,
    ledger_connection,
    live_ledger_worktree_paths,
)
from services.git_integration_worker.cursor_sdk_worktree_lock import lock_lane_worktree
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    active_pin,
    ensure_worktree_schema,
    pin_lane_worktree,
)
from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

_MISSING_SHA = "0" * 40


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    CursorDispatchLedger._instance = None
    reset_live_grade_memo_for_tests()
    yield
    CursorDispatchLedger._instance = None
    reset_live_grade_memo_for_tests()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "master", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "test", cwd=root)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    return root


def _lane_commit(repo: Path, branch: str, body: str) -> str:
    """Create *branch* with one commit and return its tip sha."""
    _git("checkout", "-b", branch, cwd=repo)
    (repo / f"{branch.replace('/', '_')}.txt").write_text(body, encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-m", f"work on {branch}", cwd=repo)
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    _git("checkout", "master", cwd=repo)
    return sha


def _verdict_for(report, branch: str):
    return next(row for row in report.verdicts if row.branch == branch)


def _admit_dispatch(
    *,
    dispatch_id: str,
    thread_id: str,
    status: str = "completed",
) -> None:
    ledger = CursorDispatchLedger.instance()
    req = CursorDispatchRequest(
        thread_id=thread_id,
        model="cursor/composer-2.5",
        dispatch_id=dispatch_id,
        execution_id=f"exec-{dispatch_id}",
        message="work",
    )
    ledger.admit(
        req=req,
        fingerprint=ledger.fingerprint(req),
        execution_id=req.execution_id,
        caller_agent=None,
        resolved_model="composer-2.5",
        admission=CursorDispatchResponse(
            admitted=True,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
            model_id="composer-2.5",
        ),
    )
    if status in {"completed", "failed", "cancelled"}:
        ledger.mark_terminal(dispatch_id=dispatch_id, terminal_status=status)
    elif status != "admitted":
        with ledger_connection() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=? WHERE dispatch_id=?",
                (status, dispatch_id),
            )


def _pin_thread(
    *, repo: Path, thread_id: str, dispatch_id: str, worktree_root: Path
) -> None:
    wt = mint_dispatch_worktree(
        source_repo=repo,
        worktree_root=worktree_root,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )
    lock_outcome = lock_lane_worktree(
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
            lock_reason=lock_outcome.lock_reason,
        )


def test_unique_patch_stays_open(repo: Path) -> None:
    """Unique commits on a live ref stay open until land or discard."""
    branch = "cursor-sdk/lane-live"
    sha = _lane_commit(repo, branch, "live\n")
    _admit_dispatch(dispatch_id="d-live", thread_id="live", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="live", dispatch_id="d-live", tip_sha=sha
    )

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open
    assert (
        _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo).returncode == 0
    )


def test_ancestor_tip_retires_row_and_keeps_ref(repo: Path) -> None:
    branch = "cursor-sdk/lane-anc"
    sha = _lane_commit(repo, branch, "merged work\n")
    _admit_dispatch(dispatch_id="d-anc", thread_id="anc", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="anc", dispatch_id="d-anc", tip_sha=sha
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_ANCESTOR
    debt = get_branch_debt(branch_name=branch)
    assert not debt.open
    assert debt.discharge_verb == "landed"
    assert (
        _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo).returncode == 0
    )


def test_cherry_equivalent_retires_row(repo: Path) -> None:
    branch = "cursor-sdk/lane-cherry"
    sha = _lane_commit(repo, branch, "cherry body\n")
    _admit_dispatch(dispatch_id="d-ch", thread_id="ch", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="ch", dispatch_id="d-ch", tip_sha=sha
    )
    _git("checkout", "master", cwd=repo)
    (repo / "master-only.txt").write_text("diverge\n", encoding="utf-8")
    _git("add", "master-only.txt", cwd=repo)
    _git("commit", "-m", "master-only", cwd=repo)
    _git("cherry-pick", sha, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_EQUIVALENT
    assert not get_branch_debt(branch_name=branch).open


def test_missing_ref_grading_unchanged(repo: Path) -> None:
    """Slice-3 absent-ref grades (archived / recovered / indeterminate)."""
    arch = "cursor-sdk/lane-arch-wrap"
    arch_sha = _lane_commit(repo, arch, "arch\n")
    open_branch_debt(
        branch_name=arch, thread_id="arch-w", dispatch_id="d-arch-w", tip_sha=arch_sha
    )
    archive_branch(repo=repo, branch_name=arch)
    _git("branch", "-D", arch, cwd=repo)
    assert (
        _verdict_for(
            reconcile_open_branch_debts(source_repo=repo, apply=False), arch
        ).verdict
        == VERDICT_ARCHIVED
    )

    rec = "cursor-sdk/lane-rec-wrap"
    rec_sha = _lane_commit(repo, rec, "rec\n")
    open_branch_debt(
        branch_name=rec, thread_id="rec-w", dispatch_id="d-rec-w", tip_sha=rec_sha
    )
    _git("branch", "-D", rec, cwd=repo)
    assert (
        _verdict_for(
            reconcile_open_branch_debts(source_repo=repo, apply=False), rec
        ).verdict
        == VERDICT_RECOVERED
    )

    gone = "cursor-sdk/lane-indet-wrap"
    open_branch_debt(
        branch_name=gone,
        thread_id="gone-w",
        dispatch_id="d-gone-w",
        tip_sha=_MISSING_SHA,
    )
    assert (
        _verdict_for(
            reconcile_open_branch_debts(source_repo=repo, apply=False), gone
        ).verdict
        == VERDICT_INDETERMINATE
    )


@pytest.mark.parametrize("status", ["running", "parked_waiting"])
def test_non_terminal_dispatch_keeps_ancestor_open(repo: Path, status: str) -> None:
    branch = "cursor-sdk/lane-nt"
    sha = _lane_commit(repo, branch, "nt\n")
    _admit_dispatch(dispatch_id="d-nt", thread_id="nt", status=status)
    open_branch_debt(
        branch_name=branch, thread_id="nt", dispatch_id="d-nt", tip_sha=sha
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


def test_sibling_non_terminal_keeps_ancestor_open(repo: Path) -> None:
    branch = "cursor-sdk/lane-sib"
    thread_id = "sib-thread"
    sha = _lane_commit(repo, branch, "sib\n")
    _admit_dispatch(dispatch_id="d-sib-parent", thread_id=thread_id, status="completed")
    _admit_dispatch(dispatch_id="d-sib-child", thread_id=thread_id, status="running")
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-sib-parent",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


def test_stale_pin_terminal_non_live_worktree_retires_ancestor(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Terminal pin on a worktree no longer in the live ledger does not guard."""
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    branch = "cursor-sdk/lane-pin-stale"
    thread_id = "pin-stale-thread"
    sha = _lane_commit(repo, branch, "pin\n")
    _admit_dispatch(dispatch_id="d-pin-stale", thread_id=thread_id, status="completed")
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin-stale",
        worktree_root=worktree_root,
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-stale",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_ANCESTOR
    assert not get_branch_debt(branch_name=branch).open


def test_non_terminal_pin_keeps_ancestor_open(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    branch = "cursor-sdk/lane-pin-nt"
    thread_id = "pin-nt-thread"
    sha = _lane_commit(repo, branch, "pinnt\n")
    _admit_dispatch(
        dispatch_id="d-pin-nt-debt", thread_id=thread_id, status="completed"
    )
    _admit_dispatch(dispatch_id="d-pin-nt-pin", thread_id=thread_id, status="claimed")
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin-nt-pin",
        worktree_root=worktree_root,
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-nt-debt",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open
    assert "active pin" in verdict.detail
    assert "d-pin-nt-pin" in verdict.detail


def test_pin_missing_dispatch_row_keeps_ancestor_open(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    branch = "cursor-sdk/lane-pin-ghost"
    thread_id = "pin-ghost-thread"
    sha = _lane_commit(repo, branch, "ghost\n")
    _admit_dispatch(
        dispatch_id="d-pin-ghost-debt", thread_id=thread_id, status="completed"
    )
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin-ghost",
        worktree_root=worktree_root,
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-ghost-debt",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open
    assert "dispatch row missing" in verdict.detail
    assert "d-pin-ghost" in verdict.detail


def test_pin_worktree_outside_configured_root_holds_ancestor(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin path outside ``GIT_INTEGRATION_WORKTREE_ROOT`` must fail closed."""
    configured_root = tmp_path / "configured-worktrees"
    outside_root = tmp_path / "outside-worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(configured_root))
    branch = "cursor-sdk/lane-pin-outside"
    thread_id = "pin-outside-thread"
    sha = _lane_commit(repo, branch, "outside\n")
    _admit_dispatch(
        dispatch_id="d-pin-outside-debt", thread_id=thread_id, status="completed"
    )
    _admit_dispatch(
        dispatch_id="d-pin-outside-pin", thread_id=thread_id, status="completed"
    )
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin-outside-pin",
        worktree_root=outside_root,
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-outside-debt",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open
    assert "active pin" in verdict.detail
    assert "d-pin-outside-pin" in verdict.detail


def test_terminal_pin_live_worktree_keeps_ancestor_open(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked

    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    branch = "cursor-sdk/lane-pin-live"
    thread_id = "pin-live-thread"
    sha = _lane_commit(repo, branch, "pinlive\n")
    _admit_dispatch(
        dispatch_id="d-pin-live-debt", thread_id=thread_id, status="completed"
    )
    pin_dispatch = "d-pin-live-park"
    _admit_dispatch(dispatch_id=pin_dispatch, thread_id=thread_id, status="running")
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id=pin_dispatch,
        worktree_root=worktree_root,
    )
    pin = active_pin(source_repo=repo, thread_id=thread_id)
    assert pin is not None
    mark_parked(
        dispatch_id=pin_dispatch,
        intent_id="intent-pin-live",
        drain_epoch=1,
        actor="manage",
        reason="deploy",
        requested_at="2026-10-13T00:00:00Z",
        method="run_cancel",
        tool_call_count=0,
        last_tool_calls=[],
        sidecar_uri=None,
    )
    pin_norm = containing_worktree_under_root(
        path=pin.worktree_path, worktree_root=worktree_root
    )
    assert pin_norm is not None
    assert pin_norm in live_ledger_worktree_paths(worktree_root=worktree_root)

    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-live-debt",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert "active pin" in verdict.detail
    assert get_branch_debt(branch_name=branch).open


def test_stale_pin_unique_patch_stays_live(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree_root = tmp_path / "worktrees"
    monkeypatch.setenv("GIT_INTEGRATION_WORKTREE_ROOT", str(worktree_root))
    branch = "cursor-sdk/lane-pin-unique"
    thread_id = "pin-unique-thread"
    sha = _lane_commit(repo, branch, "unique\n")
    _admit_dispatch(dispatch_id="d-pin-unique", thread_id=thread_id, status="completed")
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin-unique",
        worktree_root=worktree_root,
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin-unique",
        tip_sha=sha,
    )

    report = reconcile_open_branch_debts(source_repo=repo, apply=False)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert "active pin" not in verdict.detail
    assert "unique patch" in verdict.detail


def test_missing_dispatch_row_keeps_ancestor_open(repo: Path) -> None:
    branch = "cursor-sdk/lane-nodisp"
    sha = _lane_commit(repo, branch, "nodisp\n")
    open_branch_debt(
        branch_name=branch,
        thread_id="nodisp",
        dispatch_id="d-missing-row",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


def test_rev_list_merge_count_git_error_stays_live(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-zero rev-list --merges --count must not fall through to cherry (16501#7 b2)."""
    branch = "cursor-sdk/lane-merge-revlist-err"
    sha = _lane_commit(repo, branch, "revlist err\n")
    _admit_dispatch(dispatch_id="d-rle", thread_id="rle", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="rle", dispatch_id="d-rle", tip_sha=sha
    )
    _git("checkout", "master", cwd=repo)
    (repo / "master-only-rle.txt").write_text("diverge\n", encoding="utf-8")
    _git("add", "master-only-rle.txt", cwd=repo)
    _git("commit", "-m", "master-only", cwd=repo)
    _git("cherry-pick", sha, cwd=repo)
    import services.git_integration_worker.cursor_sdk_branch_debt_reconcile as mod

    real_git = mod._git

    def _broken_git(r: Path, *args: str):
        if args[:3] == ("rev-list", "--merges", "--count"):
            return subprocess.CompletedProcess(
                args, 128, "", "fatal: rev-list merge count failed"
            )
        return real_git(r, *args)

    monkeypatch.setattr(mod, "_git", _broken_git)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert "git exit 128" in verdict.detail or "rev-list merge count failed" in (
        verdict.detail
    )
    assert get_branch_debt(branch_name=branch).open


def test_rev_list_merge_count_unparsable_stays_live(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unparsable rev-list --merges --count output must grade LIVE, not cherry-through."""
    branch = "cursor-sdk/lane-merge-revlist-bad"
    sha = _lane_commit(repo, branch, "revlist bad\n")
    _admit_dispatch(dispatch_id="d-rlb", thread_id="rlb", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="rlb", dispatch_id="d-rlb", tip_sha=sha
    )
    _git("checkout", "master", cwd=repo)
    (repo / "master-only-rlb.txt").write_text("diverge\n", encoding="utf-8")
    _git("add", "master-only-rlb.txt", cwd=repo)
    _git("commit", "-m", "master-only", cwd=repo)
    _git("cherry-pick", sha, cwd=repo)
    import services.git_integration_worker.cursor_sdk_branch_debt_reconcile as mod

    real_git = mod._git

    def _bad_count_git(r: Path, *args: str):
        if args[:3] == ("rev-list", "--merges", "--count"):
            return subprocess.CompletedProcess(args, 0, "not-an-int\n", "")
        return real_git(r, *args)

    monkeypatch.setattr(mod, "_git", _bad_count_git)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_LIVE
    assert verdict.detail == "rev-list merge count unparsable"
    assert get_branch_debt(branch_name=branch).open


def test_git_error_keeps_row_open(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A git failure during grading must not retire the row (master present)."""
    branch = "cursor-sdk/lane-giterr"
    sha = _lane_commit(repo, branch, "err work\n")
    _admit_dispatch(dispatch_id="d-err", thread_id="err", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="err", dispatch_id="d-err", tip_sha=sha
    )
    import services.git_integration_worker.cursor_sdk_branch_debt_reconcile as mod

    real_git = mod._git

    def _broken_git(r: Path, *args: str):
        if args[:2] == ("merge-base", "--is-ancestor"):
            return subprocess.CompletedProcess(args, 128, "", "fatal: broken ref")
        return real_git(r, *args)

    monkeypatch.setattr(mod, "_git", _broken_git)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    verdict = _verdict_for(report, branch)
    assert verdict.verdict == VERDICT_LIVE
    assert "git exit 128" in verdict.detail or "broken ref" in verdict.detail
    assert get_branch_debt(branch_name=branch).open


def test_reconcile_git_timeout_keeps_row_open(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = "cursor-sdk/lane-timeout"
    sha = _lane_commit(repo, branch, "timeout\n")
    _admit_dispatch(dispatch_id="d-to", thread_id="to", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="to", dispatch_id="d-to", tip_sha=sha
    )
    import services.git_integration_worker.cursor_sdk_branch_debt_reconcile as mod

    real_git = mod._git
    head_ref = f"refs/heads/{branch}"

    def _slow_git(r: Path, *args: str):
        if args[:2] == ("rev-parse", head_ref):
            raise subprocess.TimeoutExpired(cmd=args, timeout=60.0)
        return real_git(r, *args)

    monkeypatch.setattr(mod, "_git", _slow_git)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    verdict = _verdict_for(report, branch)
    assert verdict.verdict == VERDICT_LIVE
    assert "branch tip could not be resolved" in verdict.detail
    assert get_branch_debt(branch_name=branch).open


def test_cherry_equivalent_with_lane_merge_stays_live(repo: Path) -> None:
    """Merge commits on the lane can carry content git cherry does not surface."""
    branch = "cursor-sdk/lane-merge-evil"
    sha_a = _lane_commit(repo, branch, "patch A\n")
    _git("checkout", "master", cwd=repo)
    (repo / "master_d.txt").write_text("D on master\n", encoding="utf-8")
    _git("add", "master_d.txt", cwd=repo)
    _git("commit", "-m", "master D", cwd=repo)
    _git("checkout", branch, cwd=repo)
    _git("merge", "master", "--no-commit", "--no-ff", cwd=repo)
    (repo / "evil.txt").write_text("not on master\n", encoding="utf-8")
    _git("add", "evil.txt", cwd=repo)
    _git("commit", "-m", "merge master with evil inside", cwd=repo)
    _git("checkout", "master", cwd=repo)
    _git("cherry-pick", sha_a, cwd=repo)
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", "master:evil.txt"],
            cwd=str(repo),
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )

    _admit_dispatch(dispatch_id="d-evil", thread_id="evil", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="evil", dispatch_id="d-evil", tip_sha=sha_a
    )

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    verdict = _verdict_for(report, branch)
    assert verdict.verdict == VERDICT_LIVE
    assert (
        verdict.detail
        == "lane merge commits may carry content not represented in cherry"
    )
    assert get_branch_debt(branch_name=branch).open


def test_live_grade_memo_requires_matching_tip_sha(repo: Path) -> None:
    """Memo must not reuse a verdict after the branch tip moves."""
    branch = "cursor-sdk/lane-memo-tip"
    sha = _lane_commit(repo, branch, "memo\n")
    _admit_dispatch(dispatch_id="d-memo", thread_id="memo", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="memo", dispatch_id="d-memo", tip_sha=sha
    )
    _git("merge", branch, cwd=repo)

    first = reconcile_open_branch_debts(source_repo=repo, apply=False)
    assert _verdict_for(first, branch).verdict == VERDICT_ANCESTOR

    _git("checkout", branch, cwd=repo)
    (repo / "after_memo.txt").write_text("new tip\n", encoding="utf-8")
    _git("add", "after_memo.txt", cwd=repo)
    _git("commit", "-m", "tip moved", cwd=repo)
    _git("checkout", "master", cwd=repo)

    second = reconcile_open_branch_debts(source_repo=repo, apply=False)
    assert _verdict_for(second, branch).verdict == VERDICT_LIVE


def test_sibling_guard_uses_dispatch_thread_when_debt_thread_missing(
    repo: Path,
) -> None:
    branch = "cursor-sdk/lane-thread-fallback"
    thread_id = "thread-from-dispatch"
    sha = _lane_commit(repo, branch, "tf\n")
    _admit_dispatch(dispatch_id="d-tf-parent", thread_id=thread_id, status="completed")
    _admit_dispatch(dispatch_id="d-tf-child", thread_id=thread_id, status="running")
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        ensure_debt_schema,
    )

    with ledger_connection() as conn:
        ensure_debt_schema(conn)
        conn.execute(
            "INSERT INTO cursor_sdk_branch_debts "
            "(branch_name, thread_id, dispatch_id, caller_agent, tip_sha, "
            "files_json, opened_at) VALUES (?, NULL, ?, NULL, ?, ?, datetime('now'))",
            (branch, "d-tf-parent", sha, "[]"),
        )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


def test_grade_cap_defers_rows(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _LIVE_GRADE_CAP
    n = cap + 5
    for i in range(n):
        branch = f"cursor-sdk/lane-cap-{i}"
        sha = _lane_commit(repo, branch, f"cap {i}\n")
        dispatch_id = f"d-cap-{i}"
        _admit_dispatch(dispatch_id=dispatch_id, thread_id=f"t-{i}", status="completed")
        open_branch_debt(
            branch_name=branch,
            thread_id=f"t-{i}",
            dispatch_id=dispatch_id,
            tip_sha=sha,
        )
        _git("merge", branch, cwd=repo)

    first = reconcile_open_branch_debts(source_repo=repo, apply=True)
    retired_first = sum(
        1 for row in first.verdicts if row.verdict == VERDICT_ANCESTOR and row.applied
    )
    assert retired_first == cap
    from services.git_integration_worker.cursor_sdk_branch_debt import list_open_debts

    assert sum(1 for d in list_open_debts() if d.open) == n - cap

    second = reconcile_open_branch_debts(source_repo=repo, apply=True)
    retired_second = sum(
        1 for row in second.verdicts if row.verdict == VERDICT_ANCESTOR and row.applied
    )
    assert retired_second == n - cap


def test_retire_path_never_deletes_ref_or_worktree(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    branch = "cursor-sdk/lane-retire-safe"
    sha = _lane_commit(repo, branch, "safe retire\n")
    _admit_dispatch(dispatch_id="d-safe", thread_id="safe", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="safe", dispatch_id="d-safe", tip_sha=sha
    )
    _git("merge", branch, cwd=repo)
    attempted: list[str] = []
    real_git = subprocess.run

    def _watch_git(*args: str, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if isinstance(cmd, list):
            joined = " ".join(cmd)
            if "branch" in joined and "-D" in joined:
                attempted.append(joined)
                raise AssertionError("branch -D must not run during reconcile retire")
            if "worktree" in joined and "remove" in joined:
                attempted.append(joined)
                raise AssertionError(
                    "worktree remove must not run during reconcile retire"
                )
        return real_git(*args, **kwargs)

    import services.git_integration_worker.cursor_sdk_branch_debt_reconcile as mod

    monkeypatch.setattr(mod.subprocess, "run", _watch_git)

    reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert get_branch_debt(branch_name=branch).discharge_verb == "landed"
    assert (
        _git("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo).returncode == 0
    )


def test_archived_tip_discharges_the_stale_row(repo: Path) -> None:
    branch = "cursor-sdk/lane-archived"
    sha = _lane_commit(repo, branch, "archived\n")
    open_branch_debt(
        branch_name=branch, thread_id="arch", dispatch_id="d-arch", tip_sha=sha
    )
    tag = archive_branch(repo=repo, branch_name=branch)
    _git("branch", "-D", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_ARCHIVED
    assert verdict.archive_tag == tag
    debt = get_branch_debt(branch_name=branch)
    assert not debt.open
    assert debt.discharge_verb == "landed"
    assert tag in debt.discharge_note


def test_reachable_orphan_tip_is_archived_then_discharged(repo: Path) -> None:
    """A tip that survives ref deletion is preserved before the row is retired."""
    branch = "cursor-sdk/lane-recover"
    sha = _lane_commit(repo, branch, "recover\n")
    open_branch_debt(
        branch_name=branch, thread_id="rec", dispatch_id="d-rec", tip_sha=sha
    )
    _git("branch", "-D", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)
    verdict = _verdict_for(report, branch)

    assert verdict.verdict == VERDICT_RECOVERED
    assert verdict.archive_tag is not None
    tagged = _git(
        "rev-parse", "--verify", f"refs/tags/{verdict.archive_tag}^{{commit}}", cwd=repo
    ).stdout.strip()
    assert tagged == sha
    assert not get_branch_debt(branch_name=branch).open


def test_unverifiable_tip_is_reported_and_never_claims_landed(repo: Path) -> None:
    """Recording ``landed`` for work we cannot inspect is worse than an open row."""
    branch = "cursor-sdk/lane-gone"
    open_branch_debt(
        branch_name=branch,
        thread_id="gone",
        dispatch_id="d-gone",
        tip_sha=_MISSING_SHA,
    )

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_INDETERMINATE
    debt = get_branch_debt(branch_name=branch)
    assert debt.open
    assert debt.discharge_verb is None


def test_indeterminate_row_does_not_suppress_aged_debt_announcement(repo: Path) -> None:
    """Stamping ``escalated_at`` here would silence the lane's only recovery route."""
    branch = "cursor-sdk/lane-gone-quiet"
    open_branch_debt(
        branch_name=branch,
        thread_id="9621",
        dispatch_id="d-9621",
        tip_sha=_MISSING_SHA,
    )

    reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert get_branch_debt(branch_name=branch).escalated_at is None


def test_dry_run_grades_without_mutating(repo: Path) -> None:
    branch = "cursor-sdk/lane-dry"
    sha = _lane_commit(repo, branch, "dry\n")
    open_branch_debt(
        branch_name=branch, thread_id="dry", dispatch_id="d-dry", tip_sha=sha
    )
    archive_branch(repo=repo, branch_name=branch)
    _git("branch", "-D", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=False)

    assert _verdict_for(report, branch).verdict == VERDICT_ARCHIVED
    assert not _verdict_for(report, branch).applied
    assert get_branch_debt(branch_name=branch).open


def test_summary_counts_every_grading(repo: Path) -> None:
    live_sha = _lane_commit(repo, "cursor-sdk/lane-a", "a\n")
    open_branch_debt(
        branch_name="cursor-sdk/lane-a",
        thread_id="lane-a",
        dispatch_id="d-lane-a",
        tip_sha=live_sha,
    )
    arch_sha = _lane_commit(repo, "cursor-sdk/lane-b", "b\n")
    open_branch_debt(
        branch_name="cursor-sdk/lane-b",
        thread_id="lane-b",
        dispatch_id="d-lane-b",
        tip_sha=arch_sha,
    )
    archive_branch(repo=repo, branch_name="cursor-sdk/lane-b")
    _git("branch", "-D", "cursor-sdk/lane-b", cwd=repo)
    open_branch_debt(
        branch_name="cursor-sdk/lane-c",
        thread_id="lane-c",
        dispatch_id="d-lane-c",
        tip_sha=_MISSING_SHA,
    )

    summary = reconcile_open_branch_debts(source_repo=repo, apply=False).summary()

    assert summary == {VERDICT_LIVE: 1, VERDICT_ARCHIVED: 1, VERDICT_INDETERMINATE: 1}
