"""Lane-B commit-on-terminal, salvage, and branch state (S3)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class BranchState:
    """Tip and merge posture for a dispatch branch keyed at mint."""

    head_sha: str | None
    commits_ahead: int
    merged_into_master: bool


@dataclass(frozen=True, slots=True)
class SalvageResult:
    """Outcome of a salvage or terminal commit attempt."""

    committed: bool
    head_sha: str | None
    commit_sha: str | None = None


def is_worktree_dirty(worktree_path: Path) -> bool:
    """True when the worktree has any porcelain delta vs HEAD."""
    proc = subprocess.run(
        ["git", "-C", str(worktree_path.resolve()), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


def _rev_parse(repo_or_wt: Path, ref: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(repo_or_wt.resolve()), "rev-parse", ref],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def salvage_commit(worktree_path: Path, *, message: str) -> SalvageResult:
    """Commit all dirty paths in the worktree; no-op when clean."""
    wt = worktree_path.resolve()
    head = _rev_parse(wt, "HEAD")
    if not is_worktree_dirty(wt):
        return SalvageResult(committed=False, head_sha=head)

    add = subprocess.run(
        ["git", "-C", str(wt), "add", "-A"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if add.returncode != 0:
        logger.warning(
            "lane_b salvage add failed path=%s err=%s",
            wt,
            add.stderr.strip(),
        )
        return SalvageResult(committed=False, head_sha=head)

    commit = subprocess.run(
        ["git", "-C", str(wt), "commit", "-m", message],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if commit.returncode != 0:
        err = commit.stderr.strip() or commit.stdout.strip()
        if "nothing to commit" in err.lower():
            return SalvageResult(committed=False, head_sha=_rev_parse(wt, "HEAD"))
        logger.warning("lane_b salvage commit failed path=%s err=%s", wt, err)
        return SalvageResult(committed=False, head_sha=_rev_parse(wt, "HEAD"))

    commit_sha = _rev_parse(wt, "HEAD")
    return SalvageResult(committed=True, head_sha=commit_sha, commit_sha=commit_sha)


def commit_on_terminal(
    *,
    dispatch_id: str,
    worktree_path: Path,
    branch_name: str,
) -> SalvageResult:
    """Durability commit at terminal after porcelain capture (Lane-B only)."""
    _ = branch_name
    message = f"cursor-sdk: lane-b terminal {dispatch_id}"
    return salvage_commit(worktree_path, message=message)


def branch_state(
    source_repo: Path,
    *,
    branch_name: str,
    branch_point: str,
) -> BranchState:
    """Resolve branch tip, commits since mint point, and merge into master."""
    repo = source_repo.resolve()
    head_sha = _rev_parse(repo, branch_name)
    if head_sha is None:
        return BranchState(head_sha=None, commits_ahead=0, merged_into_master=False)

    count_proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-list",
            "--count",
            f"{branch_point}..{branch_name}",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    commits_ahead = 0
    if count_proc.returncode == 0 and count_proc.stdout.strip().isdigit():
        commits_ahead = int(count_proc.stdout.strip())

    merged_proc = subprocess.run(
        ["git", "-C", str(repo), "branch", "--merged", "master"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    merged_names: set[str] = set()
    if merged_proc.returncode == 0:
        for line in merged_proc.stdout.splitlines():
            name = line.strip().lstrip("* ").strip()
            if name:
                merged_names.add(name)

    return BranchState(
        head_sha=head_sha,
        commits_ahead=commits_ahead,
        merged_into_master=branch_name in merged_names,
    )
