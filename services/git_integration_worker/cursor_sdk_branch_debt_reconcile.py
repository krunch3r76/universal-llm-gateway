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

Live refs whose work is already on ``master`` (ancestor tip or cherry-equivalent)
are retired **row-only** when dispatch and pin guards pass — no branch delete,
no worktree release.
"""

from __future__ import annotations

import enum
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_sdk_branch_archive import (
    ARCHIVE_TAG_PREFIX,
    lookup_archive_tag,
)
from services.git_integration_worker.cursor_sdk_branch_debt import (
    BranchDebt,
    discharge_branch_debt,
    get_branch_debt,
    list_open_debts,
)
from services.git_integration_worker.cursor_sdk_branch_debt_tags import (
    remove_land_required_tag,
)
from services.git_integration_worker.cursor_sdk_events import (
    emit_sdk_lane_b_discharged,
)
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    containing_worktree_under_root,
    ledger_connection,
    live_ledger_worktree_paths,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import (
    is_reapable_dispatch_status,
    sibling_non_terminal_dispatch_on_thread,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import active_pin

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0
_LIVE_GRADE_CAP = 20
_MASTER_REF = "master"

VERDICT_LIVE = "live"
VERDICT_ARCHIVED = "archived"
VERDICT_RECOVERED = "recovered"
VERDICT_INDETERMINATE = "indeterminate"
VERDICT_ANCESTOR = "ancestor"
VERDICT_EQUIVALENT = "equivalent"

# branch_name -> (tip_sha, master_sha, git_verdict)
_LIVE_GRADE_MEMO: dict[str, tuple[str, str, str]] = {}
_LIVE_GRADE_MEMO_MAX = 256


def reset_live_grade_memo_for_tests() -> None:
    """Clear process-local live-ref grade memo (tests only)."""
    _LIVE_GRADE_MEMO.clear()


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


@dataclass
class _SweepState:
    """Per-sweep counters and master tip cache."""

    master_sha_by_repo: dict[Path, str | None] = field(default_factory=dict)
    fresh_live_grades: int = 0
    live_ledger_paths: set[str] | None = None
    worktree_root: Path | None = None


def _live_ledger_paths_for_sweep(sweep: _SweepState) -> set[str]:
    if sweep.live_ledger_paths is None:
        root = sweep.worktree_root
        if root is None:
            raise RuntimeError("reconcile sweep missing worktree_root")
        sweep.live_ledger_paths = live_ledger_worktree_paths(worktree_root=root)
    return sweep.live_ledger_paths


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def ref_exists(repo: Path, branch_name: str) -> bool:
    """Return whether ``refs/heads/<branch_name>`` resolves in *repo*.

    Boolean for branch-debt classify. A non-zero git status is false, including
    a real git error. Callers that must not treat a git error as absence use
    ``ref_probe``.
    """
    return (
        _git(repo, "rev-parse", "--verify", f"refs/heads/{branch_name}").returncode == 0
    )


class RefProbeState(enum.Enum):
    """Whether ``refs/heads/<branch>`` is present, absent, or unverified."""

    PRESENT = "present"
    ABSENT = "absent"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RefProbeResult:
    """``ref_probe`` outcome. ``ERROR`` is a git failure, not a missing ref."""

    state: RefProbeState
    detail: str = ""


def _stderr_is_repo_failure(stderr: str) -> bool:
    lowered = stderr.lower()
    return "not a git repository" in lowered or "dubious ownership" in lowered


def ref_probe(repo: Path, branch_name: str) -> RefProbeResult:
    """Classify ``refs/heads/<branch_name>`` as present, absent, or a git error.

    ``git rev-parse --verify`` exits 128 when the ref is missing, when *repo*
    is not a repository, and on dubious ownership. ``--quiet`` maps only the
    missing-ref case to exit 1. Any other non-zero status is an error.
    """
    ref = f"refs/heads/{branch_name}"
    try:
        proc = _git(repo, "rev-parse", "--verify", "--quiet", ref)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return RefProbeResult(state=RefProbeState.ERROR, detail=str(exc))
    if proc.returncode == 0:
        return RefProbeResult(state=RefProbeState.PRESENT)
    stderr = (proc.stderr or "").strip()
    if proc.returncode == 1 and not _stderr_is_repo_failure(stderr):
        return RefProbeResult(state=RefProbeState.ABSENT, detail=stderr)
    detail = stderr or f"git exit {proc.returncode}"
    return RefProbeResult(state=RefProbeState.ERROR, detail=detail)


def commit_exists(repo: Path, sha: str) -> bool:
    """Return whether *sha* resolves to a reachable commit object."""
    if not sha:
        return False
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def _tag_orphan_tip(repo: Path, branch_name: str, sha: str) -> str | None:
    """Archive a tip whose branch ref is already gone, keyed like ``archive_branch``."""
    tag_name = f"{ARCHIVE_TAG_PREFIX}{branch_name}-{sha[:8]}"
    if _git(repo, "rev-parse", "--verify", f"refs/tags/{tag_name}").returncode == 0:
        return tag_name
    if _git(repo, "tag", tag_name, sha).returncode != 0:
        return None
    return tag_name


def _dispatch_status(dispatch_id: str | None) -> str | None:
    if not dispatch_id:
        return None
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row["status"])


def _dispatch_thread_id(dispatch_id: str | None) -> str | None:
    if not dispatch_id:
        return None
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT thread_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None or row["thread_id"] is None:
        return None
    return str(row["thread_id"])


def _head_ref(branch_name: str) -> str:
    return f"refs/heads/{branch_name}"


_MEMOIZABLE_LIVE_DETAILS = frozenset(
    {
        "unique patch commits remain on branch",
        "lane merge commits may carry content not represented in cherry",
    }
)


def _memoize_live_grade(
    branch: str, tip_sha: str, master_sha: str, git_verdict: str, git_detail: str
) -> None:
    if git_verdict == VERDICT_LIVE and git_detail not in _MEMOIZABLE_LIVE_DETAILS:
        return
    if git_verdict not in {VERDICT_ANCESTOR, VERDICT_EQUIVALENT, VERDICT_LIVE}:
        return
    while len(_LIVE_GRADE_MEMO) >= _LIVE_GRADE_MEMO_MAX:
        _LIVE_GRADE_MEMO.pop(next(iter(_LIVE_GRADE_MEMO)))
    _LIVE_GRADE_MEMO[branch] = (tip_sha, master_sha, git_verdict)


def _live_ref_guard_detail(
    *, repo: Path, debt: BranchDebt, sweep: _SweepState
) -> str | None:
    """Return guard ``detail`` when the row must stay open; ``None`` when clear."""
    status = _dispatch_status(debt.dispatch_id)
    if debt.dispatch_id is None or status is None:
        return "guard: dispatch row missing or dispatch_id unset"
    if not is_reapable_dispatch_status(status):
        return f"guard: dispatch status {status!r} not terminal"
    thread_id = debt.thread_id or _dispatch_thread_id(debt.dispatch_id) or ""
    if thread_id and debt.dispatch_id:
        sibling = sibling_non_terminal_dispatch_on_thread(
            thread_id=thread_id,
            exclude_dispatch_id=debt.dispatch_id,
        )
        if sibling is not None:
            return f"guard: sibling dispatch {sibling} still non-terminal"
    if thread_id:
        pin = active_pin(source_repo=repo, thread_id=thread_id)
        if pin is not None:
            pin_status = _dispatch_status(pin.dispatch_id)
            if pin_status is None:
                return (
                    f"guard: active pin dispatch_id={pin.dispatch_id} "
                    "dispatch row missing"
                )
            if not is_reapable_dispatch_status(pin_status):
                return f"guard: active pin dispatch_id={pin.dispatch_id}"
            root = sweep.worktree_root
            if root is None:
                return f"guard: active pin dispatch_id={pin.dispatch_id}"
            pin_path = containing_worktree_under_root(
                path=pin.worktree_path,
                worktree_root=root,
            )
            if pin_path is None:
                return f"guard: active pin dispatch_id={pin.dispatch_id}"
            if pin_path in _live_ledger_paths_for_sweep(sweep):
                return f"guard: active pin dispatch_id={pin.dispatch_id}"
    return None


def _master_sha(repo: Path, sweep: _SweepState) -> str | None:
    cached = sweep.master_sha_by_repo.get(repo)
    if repo in sweep.master_sha_by_repo:
        return cached
    try:
        proc = _git(repo, "rev-parse", _MASTER_REF)
    except (subprocess.TimeoutExpired, OSError):
        sweep.master_sha_by_repo[repo] = None
        return None
    sha = proc.stdout.strip() if proc.returncode == 0 else None
    sweep.master_sha_by_repo[repo] = sha
    return sha


def _branch_tip_sha(repo: Path, branch_name: str) -> str | None:
    try:
        proc = _git(repo, "rev-parse", _head_ref(branch_name))
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _grade_live_git(repo: Path, branch_name: str) -> tuple[str, str]:
    """Git-only grade for a present ref: ancestor, equivalent, or live."""
    head = _head_ref(branch_name)
    try:
        ancestor = _git(repo, "merge-base", "--is-ancestor", head, _MASTER_REF)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return VERDICT_LIVE, str(exc)
    if ancestor.returncode == 0:
        return VERDICT_ANCESTOR, ""
    if ancestor.returncode not in (0, 1):
        detail = (ancestor.stderr or "").strip() or f"git exit {ancestor.returncode}"
        return VERDICT_LIVE, detail

    try:
        merges = _git(repo, "rev-list", "--merges", "--count", f"{_MASTER_REF}..{head}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return VERDICT_LIVE, str(exc)
    if merges.returncode != 0:
        detail = (merges.stderr or "").strip() or f"git exit {merges.returncode}"
        return VERDICT_LIVE, detail
    try:
        merge_count = int((merges.stdout or "").strip() or "0")
    except ValueError:
        return VERDICT_LIVE, "rev-list merge count unparsable"
    if merge_count > 0:
        return (
            VERDICT_LIVE,
            "lane merge commits may carry content not represented in cherry",
        )

    try:
        cherry = _git(repo, "cherry", _MASTER_REF, head)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return VERDICT_LIVE, str(exc)
    if cherry.returncode not in (0, 1):
        detail = (cherry.stderr or "").strip() or f"git exit {cherry.returncode}"
        return VERDICT_LIVE, detail
    lines = [line for line in cherry.stdout.splitlines() if line.strip()]
    if not lines:
        return VERDICT_LIVE, "git cherry produced no output"
    if any(line.startswith("+") for line in lines):
        return VERDICT_LIVE, "unique patch commits remain on branch"
    return VERDICT_EQUIVALENT, ""


def _classify_present_ref(
    *,
    repo: Path,
    debt: BranchDebt,
    sweep: _SweepState,
) -> DebtVerdict:
    branch = debt.branch_name
    guard = _live_ref_guard_detail(repo=repo, debt=debt, sweep=sweep)
    if guard is not None:
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_LIVE,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail=guard,
        )

    master_sha = _master_sha(repo, sweep)
    if master_sha is None:
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_LIVE,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail=f"guard: {_MASTER_REF} ref missing in repo",
        )

    tip_sha = _branch_tip_sha(repo, branch)
    if tip_sha is None:
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_LIVE,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail="guard: branch tip could not be resolved",
        )
    memo = _LIVE_GRADE_MEMO.get(branch)
    if memo is not None and memo[0] == tip_sha and memo[1] == master_sha:
        git_verdict, git_detail = memo[2], ""
    else:
        if sweep.fresh_live_grades >= _LIVE_GRADE_CAP:
            return DebtVerdict(
                branch=branch,
                verdict=VERDICT_LIVE,
                thread_id=debt.thread_id,
                tip_sha=tip_sha or debt.tip_sha,
                detail="deferred: grade cap",
            )
        sweep.fresh_live_grades += 1
        git_verdict, git_detail = _grade_live_git(repo, branch)
        _memoize_live_grade(branch, tip_sha, master_sha, git_verdict, git_detail)

    return DebtVerdict(
        branch=branch,
        verdict=git_verdict,
        thread_id=debt.thread_id,
        tip_sha=tip_sha or debt.tip_sha,
        detail=git_detail,
    )


def _classify_missing_ref(*, repo: Path, debt: BranchDebt) -> DebtVerdict:
    """Grade one open debt whose branch ref is absent."""
    branch = debt.branch_name
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

    if debt.tip_sha and commit_exists(repo, debt.tip_sha):
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


def _classify(*, repo: Path, debt: BranchDebt, sweep: _SweepState) -> DebtVerdict:
    """Grade one open debt by the tip evidence that survives it."""
    branch = debt.branch_name
    probe = ref_probe(repo, branch)
    if probe.state == RefProbeState.PRESENT:
        return _classify_present_ref(repo=repo, debt=debt, sweep=sweep)
    if probe.state == RefProbeState.ERROR:
        return DebtVerdict(
            branch=branch,
            verdict=VERDICT_LIVE,
            thread_id=debt.thread_id,
            tip_sha=debt.tip_sha,
            detail=f"ref probe error: {probe.detail}",
        )
    return _classify_missing_ref(repo=repo, debt=debt)


def _resolve_retire_thread_id(verdict: DebtVerdict) -> str | None:
    if verdict.thread_id:
        return verdict.thread_id
    debt = get_branch_debt(branch_name=verdict.branch)
    if debt is None:
        return None
    return debt.thread_id or _dispatch_thread_id(debt.dispatch_id)


def _retire(*, verdict: DebtVerdict, note: str) -> DebtVerdict:
    """Discharge a reconciled debt as landed and announce it."""
    discharge_branch_debt(branch_name=verdict.branch, verb="landed", note=note)
    remove_land_required_tag(thread_id=_resolve_retire_thread_id(verdict))
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
    """Carry out the action a verdict licenses; guarded LIVE rows are never touched."""
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
    if verdict.verdict in {VERDICT_ANCESTOR, VERDICT_EQUIVALENT}:
        note = (
            "reconciled — ancestor of master"
            if verdict.verdict == VERDICT_ANCESTOR
            else "reconciled — cherry equivalent"
        )
        return _retire(verdict=verdict, note=note)
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
    living branch explains, plus live refs whose work is already on ``master``
    when guards pass.
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
    sweep = _SweepState(worktree_root=cfg.worktree_root)
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
        verdicts.append(_classify(repo=repo, debt=debt, sweep=sweep))
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
    logger.info("branch debt reconcile apply=%s summary=%s", apply, report.summary())
    return report
