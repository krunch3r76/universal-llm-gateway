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
    VERDICT_ANCESTOR,
    VERDICT_ARCHIVED,
    VERDICT_EQUIVALENT,
    VERDICT_INDETERMINATE,
    VERDICT_LIVE,
    VERDICT_RECOVERED,
    _LIVE_GRADE_CAP,
    reconcile_open_branch_debts,
    reset_live_grade_memo_for_tests,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
from services.git_integration_worker.cursor_sdk_worktree import mint_dispatch_worktree
from services.git_integration_worker.cursor_sdk_worktree_lock import lock_lane_worktree
from services.git_integration_worker.cursor_sdk_worktree_registry import (
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


def test_active_pin_keeps_ancestor_open(repo: Path, tmp_path: Path) -> None:
    branch = "cursor-sdk/lane-pin"
    thread_id = "pin-thread"
    sha = _lane_commit(repo, branch, "pin\n")
    _admit_dispatch(dispatch_id="d-pin", thread_id=thread_id, status="completed")
    _pin_thread(
        repo=repo,
        thread_id=thread_id,
        dispatch_id="d-pin",
        worktree_root=tmp_path / "worktrees",
    )
    open_branch_debt(
        branch_name=branch,
        thread_id=thread_id,
        dispatch_id="d-pin",
        tip_sha=sha,
    )
    _git("merge", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


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


def test_git_error_keeps_row_open(tmp_path: Path) -> None:
    """No ``master`` ref — git grading must not retire the row."""
    root = tmp_path / "repo-main-only"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@example.com", cwd=root)
    _git("config", "user.name", "test", cwd=root)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "seed", cwd=root)
    branch = "cursor-sdk/lane-giterr"
    _git("checkout", "-b", branch, cwd=root)
    (root / "err.txt").write_text("err\n", encoding="utf-8")
    _git("add", "err.txt", cwd=root)
    _git("commit", "-m", "err work", cwd=root)
    sha = _git("rev-parse", "HEAD", cwd=root).stdout.strip()
    _git("checkout", "main", cwd=root)
    _admit_dispatch(dispatch_id="d-err", thread_id="err", status="completed")
    open_branch_debt(
        branch_name=branch, thread_id="err", dispatch_id="d-err", tip_sha=sha
    )

    report = reconcile_open_branch_debts(source_repo=root, apply=True)

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
        1
        for row in first.verdicts
        if row.verdict == VERDICT_ANCESTOR and row.applied
    )
    assert retired_first == cap
    from services.git_integration_worker.cursor_sdk_branch_debt import list_open_debts

    assert sum(1 for d in list_open_debts() if d.open) == n - cap

    second = reconcile_open_branch_debts(source_repo=repo, apply=True)
    retired_second = sum(
        1
        for row in second.verdicts
        if row.verdict == VERDICT_ANCESTOR and row.applied
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
                raise AssertionError("worktree remove must not run during reconcile retire")
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
