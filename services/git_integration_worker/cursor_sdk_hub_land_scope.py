"""Hub-land scope from commissioner packet prose and lane-B ref reachability.

``land_lane_branch_onto_hub_master`` fast-forwards or clean-merges a lane
branch onto hub master only when the tip has an APPROVE K43 or a recorded
exemption. ``_ff_only_onto_hub_master`` and ``_clean_merge_onto_hub_master``
run that check on the sha they resolve and merge that sha.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 10.0
CURSOR_SDK_HUB_MASTER_REF_ENV = "CURSOR_SDK_HUB_MASTER_REF"
_DEFAULT_HUB_MASTER_REF = "refs/heads/master"

_UPDATING_RE = re.compile(r"Updating ([0-9a-fA-F]+)\.\.([0-9a-fA-F]+)")

_LAND_ONLY_NOT_SCOPED_OUT_RE = re.compile(
    r"files_expected\s*:\s*none\s*[—–-]\s*land\s+only\b",
    re.IGNORECASE,
)
_HUB_LAND_SCOPED_OUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"do\s+not\s+hub[- ]land", re.IGNORECASE),
    re.compile(r"out-of-scope\s*:\s*[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"out\.of\.scope\s*:\s*[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"out-of-scope[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"¬\s*hub[- ]land", re.IGNORECASE),
)


@dataclass(frozen=True)
class LaneBranchLandResult:
    """Whether a deliberate lane land moved hub master, and the ref before and after."""

    landed: bool
    before_sha: str
    after_sha: str
    refuse_reason: str | None = None


@dataclass(frozen=True)
class _FfReadyOk:
    hub: Path
    tip_sha: str


def land_lane_branch_onto_hub_master(
    repo: Path, *, branch_name: str, holder_op_id: str
) -> LaneBranchLandResult:
    """Fast-forward hub master to a lane branch while this process holds the land lease.

    Non-blocking. Another holder returns not-landed at once. A clean merge is
    the fallback when fast-forward is refused and the merge does not conflict.
    Before any ref move, the branch tip must have an ``APPROVE`` row in
    ``master_review_owed`` or a recorded ``exemption_ref``. A missing ledger
    refuses. Prechecks that already refuse (dirty overlap, gitignored path,
    non-master HEAD) keep their reasons. Does not revert an earlier land.
    Never call this to test feasibility.
    """
    from services.git_integration_worker.cursor_sdk_land_lease import (
        master_land_lease_key,
        release_land_lease_best_effort,
        try_acquire_land_lease,
    )

    hub = resolve_hub_git_repo(repo)
    before = _master_sha(hub)
    lease_key = master_land_lease_key(hub)
    holder = (holder_op_id or "").strip()
    if not holder:
        logger.error("lane land refused; holder_op_id empty hub=%s", hub)
        return LaneBranchLandResult(False, before, before)
    try:
        acquired = try_acquire_land_lease(lease_key=lease_key, holder_op_id=holder)
    except Exception:
        logger.error(
            "lane land left unlanded; master land lease acquire failed "
            "lease_key=%s branch=%s holder=%s",
            lease_key,
            branch_name,
            holder,
        )
        return LaneBranchLandResult(False, before, before)
    if not acquired:
        logger.warning(
            "lane land left unlanded; master land lease held "
            "lease_key=%s branch=%s holder=%s",
            lease_key,
            branch_name,
            holder,
        )
        return LaneBranchLandResult(False, before, before)
    refuse_reason: str | None = None
    try:
        try:
            from services.git_integration_worker.master_review_owed import (
                land_k43_block_reason,
            )

            ready = _ff_ready(repo, branch_name=branch_name)
            if isinstance(ready, _FfReadyOk):
                block = land_k43_block_reason(ready.tip_sha)
                if block:
                    logger.info(
                        "hub land refuse %s branch=%s tip=%s",
                        block,
                        branch_name,
                        ready.tip_sha,
                    )
                    return LaneBranchLandResult(
                        False, before, before, refuse_reason=block
                    )
            moved, ff_reason = _ff_only_onto_hub_master(repo, branch_name=branch_name)
            if not moved:
                refuse_reason = ff_reason
                if ff_reason == "not_ff":
                    moved, merge_reason = _clean_merge_onto_hub_master(
                        repo, branch_name=branch_name
                    )
                    if not moved:
                        refuse_reason = merge_reason or refuse_reason or "not_ff"
        except Exception:
            logger.error(
                "lane land actuator failed lease_key=%s branch=%s holder=%s",
                lease_key,
                branch_name,
                holder,
            )
            _abort_merge_if_started(hub)
            moved = False
            refuse_reason = refuse_reason or "merge_conflict"
        after = _master_sha(hub)
        landed = bool(moved) and after != before
        return LaneBranchLandResult(
            landed, before, after, refuse_reason=None if landed else refuse_reason
        )
    finally:
        release_land_lease_best_effort(lease_key=lease_key, holder_op_id=holder)


def can_ff_onto_hub_master(repo: Path, *, branch_name: str) -> bool:
    """True when a fast-forward of hub master onto ``branch_name`` would be admitted.

    Same preconditions as ``ff_only_onto_hub_master`` before it merges, plus
    ``merge-base --is-ancestor`` of hub master into the branch tip. Never runs
    ``git merge`` and never writes refs, the index, or the worktree.
    """
    ready = _ff_ready(repo, branch_name=branch_name)
    if not isinstance(ready, _FfReadyOk):
        return False
    hub = ready.hub
    ancestor = _git_capture(
        hub, "merge-base", "--is-ancestor", "refs/heads/master", branch_name.strip()
    )
    return ancestor.returncode == 0


def hub_land_precondition_reason(repo: Path, *, branch_name: str) -> str | None:
    """Return a refuse reason when hub prechecks block land, else ``None``."""
    ready = _ff_ready(repo, branch_name=branch_name)
    if isinstance(ready, str):
        return ready
    return None


def _ff_ready(repo: Path, *, branch_name: str) -> _FfReadyOk | str | None:
    """Hub and branch tip when ff pre-merge checks pass, else a reason or ``None``."""
    branch = (branch_name or "").strip()
    if not branch or branch == "master":
        return None
    hub = resolve_hub_git_repo(repo)
    head = _git_capture(hub, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != "master":
        return "hub_head_not_master"
    dirty_reason = _hub_worktree_dirty_refusal(hub, branch_name=branch)
    if dirty_reason:
        return dirty_reason
    ahead = _git_capture(hub, "rev-list", "--count", f"master..{branch}")
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit():
        return None
    if int(ahead.stdout.strip()) < 1:
        return None
    gitignored_path = _lane_history_gitignored_addition(hub, branch)
    if gitignored_path:
        logger.info(
            "hub land refuse hub_lane_gitignored_path branch=%s path=%s",
            branch,
            gitignored_path,
        )
        return "hub_lane_gitignored_path"
    tip = _git_capture(hub, "rev-parse", "--verify", f"{branch}^{{commit}}")
    if tip.returncode != 0 or not tip.stdout.strip():
        return None
    return _FfReadyOk(hub, tip.stdout.strip())


def _porcelain_dirty_paths(porcelain: str) -> list[str]:
    paths: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        body = line[3:].strip()
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        rel = body.strip()
        if rel:
            paths.append(rel)
    return paths


def _lane_land_path_names(hub: Path, branch: str) -> frozenset[str] | None:
    proc = _git_capture(hub, "diff", "--name-only", f"master...{branch}")
    if proc.returncode != 0:
        return None
    return frozenset(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _path_gitignored(hub: Path, rel_path: str) -> bool:
    from services.git_integration_worker.cursor_sdk_capture_status import (
        _path_gitignored as capture_path_gitignored,
    )

    return capture_path_gitignored(hub, rel_path)


def _lane_history_gitignored_addition(hub: Path, branch: str) -> str | None:
    """First gitignored path added on any commit in ``master..branch``."""
    rev = _git_capture(hub, "rev-list", f"master..{branch}")
    if rev.returncode != 0:
        return None
    for commit in (ln.strip() for ln in rev.stdout.splitlines() if ln.strip()):
        proc = _git_capture(
            hub, "diff-tree", "--no-commit-id", "--name-status", "-r", commit
        )
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip()
            if not status.startswith("A"):
                continue
            path = parts[-1].strip()
            if path and _path_gitignored(hub, path):
                return path
    return None


def _blob_at_branch(hub: Path, branch: str, rel_path: str) -> bytes | None:
    proc = _git_capture(hub, "show", f"{branch}:{rel_path}")
    if proc.returncode != 0:
        return None
    return proc.stdout.encode()


def _worktree_file_bytes(hub: Path, rel_path: str) -> bytes | None:
    path = hub / rel_path
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _hub_worktree_dirty_refusal(hub: Path, *, branch_name: str) -> str | None:
    """Disjoint-only dirty on hub master, matching manual S1a land discipline."""
    branch = branch_name.strip()
    land_paths = _lane_land_path_names(hub, branch)
    if land_paths is None:
        logger.info(
            "hub land refuse hub_land_paths_unknown branch=%s",
            branch,
        )
        return "hub_land_paths_unknown"
    cached = _git_capture(hub, "diff", "--cached", "--name-only")
    if cached.returncode == 0 and cached.stdout.strip():
        staged = [line.strip() for line in cached.stdout.splitlines() if line.strip()]
        logger.info(
            "hub land refuse hub_index_staged dirty_paths=%s",
            staged,
        )
        return "hub_index_staged"
    porcelain = _git_capture(hub, "status", "--porcelain=v1")
    if porcelain.returncode != 0:
        return "hub_dirty_overlap"
    dirty_paths = _porcelain_dirty_paths(porcelain.stdout)
    if not porcelain.stdout.strip():
        return None
    for line in porcelain.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        body = line[3:].strip()
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        rel = body.strip()
        if not rel:
            continue
        if rel not in land_paths:
            continue
        if xy == "??":
            logger.info(
                "hub land refuse hub_dirty_overlap dirty_paths=%s",
                dirty_paths,
            )
            return "hub_dirty_overlap"
        hub_bytes = _worktree_file_bytes(hub, rel)
        branch_bytes = _blob_at_branch(hub, branch, rel)
        if hub_bytes != branch_bytes:
            logger.info(
                "hub land refuse hub_dirty_overlap dirty_paths=%s",
                dirty_paths,
            )
            return "hub_dirty_overlap"
    if dirty_paths:
        logger.info(
            "hub land proceed disjoint dirty_paths=%s",
            dirty_paths,
        )
    return None


def _k43_block(tip_sha: str, *, actuator: str) -> str | None:
    """Return the K43 refuse reason for ``tip_sha``, or ``None`` when it may merge.

    Calls ``land_k43_block_reason``. A block is logged and returned so the
    caller does not run ``git merge``. Ledger unreadability fails closed.
    """
    from services.git_integration_worker.master_review_owed import (
        land_k43_block_reason,
    )

    block = land_k43_block_reason(tip_sha)
    if block:
        logger.info("hub land refuse %s actuator=%s tip=%s", block, actuator, tip_sha)
    return block


def _refuse_without_land_lease(hub: Path, actuator: str) -> bool:
    """True when this process must not move hub master."""
    from services.git_integration_worker.cursor_sdk_land_lease import (
        process_holds_master_land_lease,
    )

    if process_holds_master_land_lease(hub):
        return False
    logger.warning(
        "%s refused; this process does not hold the master land lease hub=%s",
        actuator,
        hub,
    )
    return True


def ff_only_onto_hub_master(repo: Path, *, branch_name: str) -> bool:
    """Fast-forward the hub ``master`` checkout to ``branch_name``.

    Returns True only when that branch tip is an ancestor of hub master afterward.
    Resolves the tip sha, refuses unless that sha has an APPROVE K43 or a
    recorded exemption, then runs ``git merge --ff-only`` of that sha. A dirty
    hub tree, a non-fast-forward, a missing ref, a branch with no commits ahead
    of master, or a K43 refusal returns False and leaves HEAD where it was.
    """
    moved, _reason = _ff_only_onto_hub_master(repo, branch_name=branch_name)
    return moved


def _ff_only_onto_hub_master(
    repo: Path, *, branch_name: str
) -> tuple[bool, str | None]:
    branch = (branch_name or "").strip()
    ready = _ff_ready(repo, branch_name=branch)
    if isinstance(ready, str):
        return False, ready
    if not isinstance(ready, _FfReadyOk):
        return False, None
    hub, tip_sha = ready.hub, ready.tip_sha
    if _refuse_without_land_lease(hub, "ff_only_onto_hub_master"):
        return False, None
    blocked = _k43_block(tip_sha, actuator="ff_only_onto_hub_master")
    if blocked:
        return False, blocked
    pre_sha = _master_sha(hub)
    merged = _git_capture(hub, "merge", "--ff-only", tip_sha)
    if merged.returncode != 0:
        return False, "not_ff"
    post_sha = _master_sha(hub)
    if post_sha and post_sha != pre_sha:
        before_sha, after_sha = _ff_range(hub, merged.stdout, tip_sha)
        _record_hub_master_move(hub, before_sha, after_sha, "ff_only_onto_hub_master")
    if commit_is_ancestor_of_hub_master(hub, tip_sha) is not True:
        return False, "not_ff"
    return True, None


def clean_merge_onto_hub_master(repo: Path, *, branch_name: str) -> bool:
    """Merge *branch_name* into hub master when git reports no conflict.

    The fast-forward actuator refuses the ordinary case: master has commits
    the lane does not. This lands that case only when the merge is textual
    and conflict-free. Resolves the tip sha, refuses unless that sha has an
    APPROVE K43 or a recorded exemption, then runs ``git merge --no-edit`` of
    that sha. A dirty hub tree, a missing ref, a non-master checkout, a branch
    with nothing ahead, a branch that is not actually behind master, or a K43
    refusal returns False and leaves HEAD where it was. A conflict aborts the
    merge and leaves HEAD where it was. Returns True only when the branch tip
    is an ancestor of hub master afterward.
    """
    moved, _reason = _clean_merge_onto_hub_master(repo, branch_name=branch_name)
    return moved


def _clean_merge_onto_hub_master(
    repo: Path, *, branch_name: str
) -> tuple[bool, str | None]:
    branch = (branch_name or "").strip()
    if not branch or branch == "master":
        return False, None
    hub = resolve_hub_git_repo(repo)
    if _refuse_without_land_lease(hub, "clean_merge_onto_hub_master"):
        return False, None
    head = _git_capture(hub, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != "master":
        return False, "hub_head_not_master"
    dirty_reason = _hub_worktree_dirty_refusal(hub, branch_name=branch)
    if dirty_reason:
        return False, dirty_reason
    ahead = _git_capture(hub, "rev-list", "--count", f"master..{branch}")
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit():
        return False, None
    if int(ahead.stdout.strip()) < 1:
        return False, None
    behind = _git_capture(hub, "rev-list", "--count", f"{branch}..master")
    if behind.returncode != 0 or not behind.stdout.strip().isdigit():
        return False, None
    if int(behind.stdout.strip()) < 1:
        return False, None
    gitignored_path = _lane_history_gitignored_addition(hub, branch)
    if gitignored_path:
        logger.info(
            "hub land refuse hub_lane_gitignored_path branch=%s path=%s",
            branch,
            gitignored_path,
        )
        return False, "hub_lane_gitignored_path"
    tip = _git_capture(hub, "rev-parse", "--verify", f"{branch}^{{commit}}")
    if tip.returncode != 0 or not tip.stdout.strip():
        return False, None
    pre_sha = _master_sha(hub)
    tip_sha = tip.stdout.strip()
    blocked = _k43_block(tip_sha, actuator="clean_merge_onto_hub_master")
    if blocked:
        return False, blocked
    merged = _git_capture(hub, "merge", "--no-edit", tip_sha)
    if merged.returncode != 0:
        _abort_merge_if_started(hub)
        combined = f"{merged.stdout}\n{merged.stderr}"
        if "CONFLICT" in combined:
            return False, "merge_conflict"
        return False, "merge_conflict"
    post_sha = _master_sha(hub)
    if post_sha and post_sha != pre_sha:
        before_sha, after_sha = _clean_merge_range(hub, tip_sha, post_sha)
        _record_hub_master_move(
            hub, before_sha, after_sha, "clean_merge_onto_hub_master"
        )
    if commit_is_ancestor_of_hub_master(hub, tip_sha) is not True:
        return False, "merge_conflict"
    return True, None


def _master_sha(hub: Path) -> str:
    proc = _git_capture(hub, "rev-parse", "refs/heads/master")
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _full_sha(hub: Path, rev: str) -> str:
    proc = _git_capture(hub, "rev-parse", "--verify", f"{rev}^{{commit}}")
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def _ff_range(hub: Path, merge_stdout: str, tip_sha: str) -> tuple[str, str]:
    """Before/after from this merge's Updating line. After is the branch tip."""
    match = _UPDATING_RE.search(merge_stdout)
    if match:
        before = _full_sha(hub, match.group(1))
        after = _full_sha(hub, match.group(2))
        if before and after == tip_sha:
            return before, after
    return "", tip_sha


def _clean_merge_range(hub: Path, tip_sha: str, post_sha: str) -> tuple[str, str]:
    """Before is the first parent when the second parent is the landed tip."""
    first = _full_sha(hub, f"{post_sha}^1")
    second = _full_sha(hub, f"{post_sha}^2")
    if first and second == tip_sha:
        return first, post_sha
    return "", post_sha


def _record_hub_master_move(
    hub: Path, before_sha: str, after_sha: str, land_path: str
) -> None:
    """Receipt after the ref moved. Failure here does not undo the move."""
    try:
        from implement_admission.restart_owed import record_land_restart_receipt

        record_land_restart_receipt(hub, before_sha, after_sha, land_path)
    except Exception:
        return


def _abort_merge_if_started(hub: Path) -> None:
    merging = _git_capture(hub, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    if merging.returncode == 0:
        _git_capture(hub, "merge", "--abort")


def _git_capture(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )


def packet_hub_land_scoped_out(prose: str | None) -> bool:
    """True when the packet explicitly excludes hub land from this dispatch's scope.

    ``files_expected: none — land only`` is **not** scoped-out (land is the work).
    """
    if not prose:
        return False
    if _LAND_ONLY_NOT_SCOPED_OUT_RE.search(prose):
        return False
    return any(pat.search(prose) for pat in _HUB_LAND_SCOPED_OUT_PATTERNS)


def resolve_hub_master_ref(*, hub_repo: Path | None = None) -> str:
    """Hub ``master`` ref for landed probes (override via ``CURSOR_SDK_HUB_MASTER_REF``)."""
    _ = hub_repo
    return os.environ.get(CURSOR_SDK_HUB_MASTER_REF_ENV, _DEFAULT_HUB_MASTER_REF)


def resolve_hub_git_repo(from_repo: Path) -> Path:
    """Return a hub checkout path for master ancestry probes from a lane worktree."""
    repo = from_repo.resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return repo
    if proc.returncode != 0:
        return repo
    lines = proc.stdout.splitlines()
    first_worktree: Path | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("worktree "):
            idx += 1
            continue
        wt_path = Path(line.split(" ", 1)[1].strip())
        if first_worktree is None:
            first_worktree = wt_path
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("worktree "):
            if lines[idx].startswith("branch refs/heads/master"):
                return wt_path
            idx += 1
    return first_worktree or repo


def commit_is_ancestor_of_hub_master(
    hub_repo: Path,
    sha: str,
    *,
    master_ref: str | None = None,
) -> bool | None:
    """True/False when hub master resolves; ``None`` when the ref is absent."""
    normalized = (sha or "").strip().lower()
    if not normalized:
        return None
    git_repo = resolve_hub_git_repo(hub_repo)
    ref = master_ref or resolve_hub_master_ref(hub_repo=git_repo)
    try:
        verify = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "rev-parse",
                "--quiet",
                "--verify",
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if verify.returncode != 0:
        return None
    try:
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "merge-base",
                "--is-ancestor",
                normalized,
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ancestor.returncode == 0


def resolve_hub_master_tip_meter(
    hub_repo: Path, *, branch_point: str
) -> tuple[str, int] | None:
    """Return ``(hub master tip, commits since branch_point)``.

    ``None`` when the hub master ref is absent or the range cannot be counted.
    Callers project closeout ``head_sha`` onto this tip once a dispatch commit
    is an ancestor and later hub commits sit above it.
    """
    base = (branch_point or "").strip().lower()
    if not base:
        return None
    git_repo = resolve_hub_git_repo(hub_repo)
    ref = resolve_hub_master_ref(hub_repo=git_repo)
    try:
        tip_proc = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if tip_proc.returncode != 0:
        return None
    tip = tip_proc.stdout.strip().lower()
    if not tip:
        return None
    try:
        count_proc = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "rev-list",
                "--count",
                f"{base}..{tip}",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if count_proc.returncode != 0 or not count_proc.stdout.strip().isdigit():
        return None
    return tip, int(count_proc.stdout.strip())


def commit_reachable_on_branch_ref(
    source_repo: Path,
    *,
    branch_name: str,
    sha: str,
) -> bool:
    """True when *sha* exists and lies on ``refs/heads/<branch_name>`` history."""
    normalized = (sha or "").strip().lower()
    if not normalized:
        return False
    repo = source_repo.resolve()
    try:
        verify = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--quiet",
                "--verify",
                f"{normalized}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if verify.returncode != 0:
        return False
    ref = f"refs/heads/{branch_name}"
    try:
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                normalized,
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ancestor.returncode == 0
