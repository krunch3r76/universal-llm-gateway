"""Reconcile on-disk worktrees against the registry.

Every sweep in this service iterated registry rows, so a tree that git knows
about but the registry does not was invisible to all of them — it sat under
``worktree_root``, pinned its branch against deletion, and no sweep could even
see it. The registry is a record of what we minted; ``git worktree list`` is the
ground truth of what exists, and reconciling to the latter is what closes the
blind spot.

Clean unregistered ``cursor-sdk/*`` (or detached ``lane-*``) trees are archived
and removed. Dirty ones are never touched: on a shared checkout an
unattributable dirty tree is parallel WIP until proven otherwise, so it is
surfaced as debt instead. ``arc/*`` and other non-sdk trees under the shared
worktree root are left alone.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 60.0


@dataclass(frozen=True, slots=True)
class GitWorktree:
    """One entry from ``git worktree list --porcelain``."""

    path: Path
    branch: str | None
    detached: bool


def list_git_worktrees(*, source_repo: Path) -> list[GitWorktree]:
    """Parse ``git worktree list --porcelain``, main checkout excluded."""
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning("git worktree list failed: %s", proc.stderr.strip())
        return []
    out: list[GitWorktree] = []
    path: Path | None = None
    branch: str | None = None
    detached = False

    def flush() -> None:
        nonlocal path, branch, detached
        if path is not None and path != source_repo.resolve():
            out.append(GitWorktree(path=path, branch=branch, detached=detached))
        path, branch, detached = None, None, False

    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            flush()
            path = Path(line[len("worktree ") :].strip()).resolve()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            branch = ref.removeprefix("refs/heads/")
        elif line.strip() == "detached":
            detached = True
    flush()
    return out


def _is_dirty(worktree: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        # Unreadable state is not provably clean; treat as dirty and leave it.
        return True
    return bool(proc.stdout.strip())


def _remove_worktree(*, source_repo: Path, worktree: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(source_repo.resolve()), "worktree", "remove", str(worktree)],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        logger.warning(
            "unregistered worktree remove failed path=%s err=%s",
            worktree,
            proc.stderr.strip(),
        )
        return False
    return True


def reconcile_unregistered_worktrees(
    *,
    source_repo: Path,
    worktree_root: Path,
    active: set[str] | None = None,
) -> tuple[int, int]:
    """Archive-and-remove clean unregistered trees; surface dirty ones as debt.

    Returns ``(reconciled, surfaced)``.
    """
    from services.git_integration_worker.cursor_sdk_branch_archive import (
        archive_branch,
    )
    from services.git_integration_worker.cursor_sdk_worktree_gc import (
        is_lane_b_reconcile_target,
        registered_branch_names,
    )

    repo = source_repo.resolve()
    root = worktree_root.resolve()
    active_paths = active or set()
    registered_branches = registered_branch_names()
    registered_paths = _registered_worktree_paths()

    reconciled = 0
    surfaced = 0
    for entry in list_git_worktrees(source_repo=repo):
        if not entry.path.is_relative_to(root):
            continue
        if not is_lane_b_reconcile_target(branch=entry.branch, path=entry.path):
            continue
        resolved = str(entry.path)
        if resolved in active_paths or resolved in registered_paths:
            continue
        if entry.branch and entry.branch in registered_branches:
            continue
        if entry.path.is_dir() and _is_dirty(entry.path):
            surfaced += _surface_dirty_tree(entry)
            continue
        if entry.branch:
            archive_branch(repo=repo, branch_name=entry.branch)
        if _remove_worktree(source_repo=repo, worktree=entry.path):
            reconciled += 1
            logger.info(
                "unregistered worktree reconciled path=%s branch=%s",
                entry.path,
                entry.branch,
            )
    return reconciled, surfaced


def _surface_dirty_tree(entry: GitWorktree) -> int:
    """Record a dirty unregistered tree as debt rather than touching it."""
    from services.git_integration_worker.cursor_sdk_branch_debt import (
        get_branch_debt,
        open_branch_debt,
    )

    branch = entry.branch or f"(detached){entry.path.name}"
    if get_branch_debt(branch_name=branch) is not None:
        return 0
    open_branch_debt(
        branch_name=branch,
        caller_agent="(unregistered worktree)",
        files=[str(entry.path)],
    )
    logger.warning(
        "unregistered dirty worktree surfaced as debt path=%s branch=%s",
        entry.path,
        entry.branch,
    )
    return 1


def _registered_worktree_paths() -> set[str]:
    from services.git_integration_worker.cursor_dispatch_ledger import _connect
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        ensure_worktree_schema,
    )

    with _connect() as conn:
        ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT worktree_path FROM cursor_sdk_lane_worktrees"
        ).fetchall()
    return {str(Path(row["worktree_path"]).resolve()) for row in rows}
