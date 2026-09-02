"""Reconcile open branch debts whose branch ref no longer exists.

Both in-service delete paths are closed loops: ``discharge`` archives then
retires the debt, and ``_delete_orphan_branch`` refuses outright while a debt is
open. Neither can produce an open debt pointing at a missing ref — yet the
ledger accumulates them, because a branch deleted out of band (a hand-run
``git branch -D``, a manual worktree teardown) leaves a row nothing ever
revisits. Every sweep asked "which branches need discharging"; none asked
"which debts still have a branch".

That absence is the defect this module closes. A debt whose ref is gone is
resolved by what survives of its tip, never by its age: an archive tag means the
work is preserved and the row is stale bookkeeping; a still-reachable commit can
be archived now and then retired; an unreachable one is **indeterminate** and is
escalated rather than discharged, because recording ``landed`` for work we can
no longer inspect is the one outcome worse than leaving the row open.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_archive import (
    ARCHIVE_TAG_PREFIX,
    lookup_archive_tag,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    BranchDebt,
    discharge_branch_debt,
    list_open_debts,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_discharged,
)

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0

VERDICT_LIVE = "live"
VERDICT_ARCHIVED = "archived"
VERDICT_RECOVERED = "recovered"
VERDICT_INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class DebtVerdict:
    """What a single open debt's surviving tip evidence supports."""

    branch: str
    verdict: str
    thread_id: str | None = None
    tip_sha: str | None = None
    archive_tag: str | None = None
    applied: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Outcome of one reconciliation sweep."""

    verdicts: list[DebtVerdict]
    applied: bool

    def by_verdict(self, verdict: str) -> list[DebtVerdict]:
        """Verdicts of one kind, in sweep order."""
        return [row for row in self.verdicts if row.verdict == verdict]

    def summary(self) -> dict[str, int]:
        """Count per verdict, for logs and the route payload."""
        counts: dict[str, int] = {}
        for row in self.verdicts:
            counts[row.verdict] = counts.get(row.verdict, 0) + 1
        return counts


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def _ref_exists(repo: Path, branch_name: str) -> bool:
    return _git(repo, "rev-parse", "--verify", f"refs/heads/{branch_name}").returncode == 0


def _commit_exists(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _tag_orphan_tip(repo: Path, branch_name: str, sha: str) -> str | None:
    """Archive a tip whose branch ref is already gone, keyed like ``archive_branch``."""
    tag_name = f"{ARCHIVE_TAG_PREFIX}{branch_name}-{sha[:8]}"
    if _git(repo, "rev-parse", "--verify", f"refs/tags/{tag_name}").returncode == 0:
        return tag_name
    if _git(repo, "tag", tag_name, sha).returncode != 0:
        return None
    return tag_name


def _classify(*, repo: Path, debt: BranchDebt) -> DebtVerdict:
    """Grade one open debt by the tip evidence that survives it."""
    branch = debt.branch_name
    if _ref_exists(repo, branch):
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_LIVE,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail="branch ref present — resolve by land or discard, not reconciliation",
        )

    archived = lookup_archive_tag(repo=repo, branch_name=branch)
    if archived is not None:
        tag_name, tag_sha = archived
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_ARCHIVED,
            thread_id=debt.thread_id,
            tip_sha=tag_sha or debt.tip_sha,
            archive_tag=tag_name,
            detail=f"ref gone; tip preserved at {tag_name}",
        )

    if debt.tip_sha and _commit_exists(repo, debt.tip_sha):
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_RECOVERED,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail="ref gone, no archive tag, tip still reachable — archivable now",
        )

    return DebtVerdict(
        branch=branch,
        verdict=VERDICT_INDETERMINATE,
        thread_id=debt.thread_id,
        tip_sha=debt.tip_sha,
        detail=(
            "ref gone, no archive tag, tip unreachable — landedness is no longer "
            "checkable from git; recover from the lane's closeout turns"
        ),
    )


def _retire(*, verdict: DebtVerdict, note: str) -> DebtVerdict:
    """Discharge a reconciled debt as landed and announce it."""
    discharge_branch_debt(branch_name=verdict.branch, verb="landed", note=note)
    emit_sdk_lane_b_discharged(
        branch=verdict.branch,
        verb="landed",
        tip_sha=verdict.tip_sha,
        archive_tag=verdict.archive_tag,
        thread_id=verdict.thread_id,
        note=note,
    )
    logger.info(
        "branch debt reconciled branch=%s verdict=%s archive=%s",
        verdict.branch,
        verdict.verdict,
        verdict.archive_tag,
    )
    return DebtVerdict(
        branch=verdict.branch,
        verdict=verdict.verdict,
        thread_id=verdict.thread_id,
        tip_sha=verdict.tip_sha,
        archive_tag=verdict.archive_tag,
        applied=True,
        detail=verdict.detail,
    )


def _apply(*, repo: Path, verdict: DebtVerdict) -> DebtVerdict:
    """Carry out the action a verdict licenses; LIVE rows are never touched."""
    if verdict.verdict == VERDICT_ARCHIVED:
        return _retire(
            verdict=verdict,
            note=f"reconciled — branch ref absent, tip preserved at {verdict.archive_tag}",
        )
    if verdict.verdict == VERDICT_RECOVERED:
        tag_name = _tag_orphan_tip(repo, verdict.branch, verdict.tip_sha or "")
        if tag_name is None:
            logger.warning(
                "orphan tip archive failed — leaving debt open branch=%s",
                verdict.branch,
            )
            return verdict
        tagged = DebtVerdict(
            branch=verdict.branch,
            verdict=verdict.verdict,
            thread_id=verdict.thread_id,
            tip_sha=verdict.tip_sha,
            archive_tag=tag_name,
            detail=verdict.detail,
        )
        return _retire(
            verdict=tagged,
            note=f"reconciled — branch ref absent, orphan tip archived at {tag_name}",
        )
    if verdict.verdict == VERDICT_INDETERMINATE:
        # Deliberately no row mutation. `mark_debt_escalated` is the aged-debt
        # loop's already-announced flag; stamping it here would suppress the bus
        # turn that tells the owning lane its branch is gone — the only route
        # left to recover work whose tip git can no longer produce. The row stays
        # open because it genuinely is unresolved; the report is what changes.
        logger.warning(
            "branch debt indeterminate branch=%s thread=%s tip=%s",
            verdict.branch,
            verdict.thread_id,
            verdict.tip_sha,
        )
    return verdict


def reconcile_open_branch_debts(
    *,
    source_repo: Path,
    apply: bool = False,
) -> ReconcileReport:
    """Grade every open debt against its surviving tip; act only when *apply*.

    Defaults to a dry run so the verdict table can be inspected before any row
    is retired — the rows this sweep resolves are, by construction, ones no
    living branch explains.
    """
    from services.git_integration_worker.config import load_config
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        resolve_debt_source_repo,
    )

    cfg = load_config()
    hub = cfg.source_repo.resolve()
    projects_root = cfg.dispatch_workspace
    sweep_repo = source_repo.resolve()
    debts = list_open_debts()
    verdicts: list[DebtVerdict] = []
    for debt in debts:
        if debt.source_repo:
            repo = resolve_debt_source_repo(
                debt.source_repo,
                hub=hub,
                projects_root=projects_root,
            )
        else:
            repo = sweep_repo
        verdicts.append(_classify(repo=repo, debt=debt))
    if apply:
        verdicts = [
            _apply(
                repo=(
                    resolve_debt_source_repo(
                        debt.source_repo,
                        hub=hub,
                        projects_root=projects_root,
                    )
                    if debt.source_repo
                    else sweep_repo
                ),
                verdict=row,
            )
            for debt, row in zip(debts, verdicts, strict=True)
        ]
    report = ReconcileReport(verdicts=verdicts, applied=apply)
    logger.info(
        "branch debt reconcile apply=%s summary=%s", apply, report.summary()
    )
    return report
