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
    VERDICT_ARCHIVED,
    VERDICT_INDETERMINATE,
    VERDICT_LIVE,
    VERDICT_RECOVERED,
    reconcile_open_branch_debts,
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
    yield
    CursorDispatchLedger._instance = None


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


def test_live_branch_is_never_touched(repo: Path) -> None:
    """A ref that still exists is land-or-discard work, not reconciliation's call."""
    branch = "cursor-sdk/lane-live"
    sha = _lane_commit(repo, branch, "live\n")
    open_branch_debt(branch_name=branch, thread_id="live", tip_sha=sha)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_LIVE
    assert get_branch_debt(branch_name=branch).open


def test_archived_tip_discharges_the_stale_row(repo: Path) -> None:
    branch = "cursor-sdk/lane-archived"
    sha = _lane_commit(repo, branch, "archived\n")
    open_branch_debt(branch_name=branch, thread_id="arch", tip_sha=sha)
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
    open_branch_debt(branch_name=branch, thread_id="rec", tip_sha=sha)
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
    open_branch_debt(branch_name=branch, thread_id="gone", tip_sha=_MISSING_SHA)

    report = reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert _verdict_for(report, branch).verdict == VERDICT_INDETERMINATE
    debt = get_branch_debt(branch_name=branch)
    assert debt.open
    assert debt.discharge_verb is None


def test_indeterminate_row_does_not_suppress_aged_debt_announcement(repo: Path) -> None:
    """Stamping ``escalated_at`` here would silence the lane's only recovery route."""
    branch = "cursor-sdk/lane-gone-quiet"
    open_branch_debt(branch_name=branch, thread_id="9621", tip_sha=_MISSING_SHA)

    reconcile_open_branch_debts(source_repo=repo, apply=True)

    assert get_branch_debt(branch_name=branch).escalated_at is None


def test_dry_run_grades_without_mutating(repo: Path) -> None:
    branch = "cursor-sdk/lane-dry"
    sha = _lane_commit(repo, branch, "dry\n")
    open_branch_debt(branch_name=branch, thread_id="dry", tip_sha=sha)
    archive_branch(repo=repo, branch_name=branch)
    _git("branch", "-D", branch, cwd=repo)

    report = reconcile_open_branch_debts(source_repo=repo, apply=False)

    assert _verdict_for(report, branch).verdict == VERDICT_ARCHIVED
    assert not _verdict_for(report, branch).applied
    assert get_branch_debt(branch_name=branch).open


def test_summary_counts_every_grading(repo: Path) -> None:
    live_sha = _lane_commit(repo, "cursor-sdk/lane-a", "a\n")
    open_branch_debt(branch_name="cursor-sdk/lane-a", tip_sha=live_sha)
    arch_sha = _lane_commit(repo, "cursor-sdk/lane-b", "b\n")
    open_branch_debt(branch_name="cursor-sdk/lane-b", tip_sha=arch_sha)
    archive_branch(repo=repo, branch_name="cursor-sdk/lane-b")
    _git("branch", "-D", "cursor-sdk/lane-b", cwd=repo)
    open_branch_debt(branch_name="cursor-sdk/lane-c", tip_sha=_MISSING_SHA)

    summary = reconcile_open_branch_debts(source_repo=repo, apply=False).summary()

    assert summary == {VERDICT_LIVE: 1, VERDICT_ARCHIVED: 1, VERDICT_INDETERMINATE: 1}
