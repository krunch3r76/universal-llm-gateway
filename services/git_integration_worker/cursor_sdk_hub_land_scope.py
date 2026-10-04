"""Hub-land scope from commissioner packet prose and lane-B ref reachability."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

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


def ff_only_onto_hub_master(repo: Path, *, branch_name: str) -> bool:
    """Fast-forward the hub ``master`` checkout to ``branch_name``.

    Returns True only when that branch is an ancestor of hub master afterward.
    A dirty hub tree, a non-fast-forward, a missing ref, or a branch with no
    commits ahead of master returns False and leaves HEAD where it was.
    Side effects: ``git merge --ff-only`` on the hub master worktree when the
    preconditions hold.
    """
    branch = (branch_name or "").strip()
    if not branch or branch == "master":
        return False
    hub = resolve_hub_git_repo(repo)
    head = _git_capture(hub, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != "master":
        return False
    porcelain = _git_capture(hub, "status", "--porcelain=v1")
    if porcelain.returncode != 0 or porcelain.stdout.strip():
        return False
    ahead = _git_capture(hub, "rev-list", "--count", f"master..{branch}")
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit():
        return False
    if int(ahead.stdout.strip()) < 1:
        return False
    tip = _git_capture(hub, "rev-parse", "--verify", f"{branch}^{{commit}}")
    if tip.returncode != 0 or not tip.stdout.strip():
        return False
    pre_sha = _master_sha(hub)
    tip_sha = tip.stdout.strip()
    merged = _git_capture(hub, "merge", "--ff-only", branch)
    if merged.returncode != 0:
        return False
    post_sha = _master_sha(hub)
    if post_sha and post_sha != pre_sha:
        before_sha, after_sha = _ff_range(hub, merged.stdout, tip_sha)
        _record_hub_master_move(
            hub, before_sha, after_sha, "ff_only_onto_hub_master"
        )
    if commit_is_ancestor_of_hub_master(hub, tip_sha) is not True:
        return False
    return True


def clean_merge_onto_hub_master(repo: Path, *, branch_name: str) -> bool:
    """Merge *branch_name* into hub master when git reports no conflict.

    The fast-forward actuator refuses the ordinary case: master has commits
    the lane does not. This lands that case only when the merge is textual
    and conflict-free. A dirty hub tree, a missing ref, a non-master checkout,
    a branch with nothing ahead, or a branch that is not actually behind
    master returns False and leaves HEAD where it was. A conflict aborts the
    merge and leaves HEAD where it was. Returns True only when the branch tip
    is an ancestor of hub master afterward.
    Side effects: ``git merge --no-edit`` on the hub master worktree when the
    preconditions hold and the merge does not conflict.
    """
    branch = (branch_name or "").strip()
    if not branch or branch == "master":
        return False
    hub = resolve_hub_git_repo(repo)
    head = _git_capture(hub, "rev-parse", "--abbrev-ref", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != "master":
        return False
    porcelain = _git_capture(hub, "status", "--porcelain=v1")
    if porcelain.returncode != 0 or porcelain.stdout.strip():
        return False
    ahead = _git_capture(hub, "rev-list", "--count", f"master..{branch}")
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit():
        return False
    if int(ahead.stdout.strip()) < 1:
        return False
    behind = _git_capture(hub, "rev-list", "--count", f"{branch}..master")
    if behind.returncode != 0 or not behind.stdout.strip().isdigit():
        return False
    if int(behind.stdout.strip()) < 1:
        return False
    tip = _git_capture(hub, "rev-parse", "--verify", f"{branch}^{{commit}}")
    if tip.returncode != 0 or not tip.stdout.strip():
        return False
    pre_sha = _master_sha(hub)
    tip_sha = tip.stdout.strip()
    merged = _git_capture(hub, "merge", "--no-edit", branch)
    if merged.returncode != 0:
        _abort_merge_if_started(hub)
        return False
    post_sha = _master_sha(hub)
    if post_sha and post_sha != pre_sha:
        before_sha, after_sha = _clean_merge_range(hub, tip_sha, post_sha)
        _record_hub_master_move(
            hub, before_sha, after_sha, "clean_merge_onto_hub_master"
        )
    if commit_is_ancestor_of_hub_master(hub, tip_sha) is not True:
        return False
    return True


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
