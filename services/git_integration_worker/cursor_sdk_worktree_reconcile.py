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
    from services.git_integration_worker.cursor_sdk_worktree_release import (
        release_lane_worktree,
    )

    result = release_lane_worktree(
        source_repo=source_repo,
        worktree_path=worktree,
        reason="reconcile",
        unregistered=True,
    )
    return result.released


_vanished_reported: set[str] = set()


def reset_vanished_pin_reports() -> None:
    """Clear vanish-sweep dedupe (tests only)."""
    _vanished_reported.clear()


def sweep_vanished_pinned_worktrees(*, source_repo: Path) -> int:
    """Emit when a ULG-locked worktree path no longer exists (Leg E)."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_pinned_worktree_vanished,
    )
    from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
        ledger_connection,
        worktree_held_by_live_bridge,
    )
    from services.git_integration_worker.cursor_sdk_worktree_lock import (
        list_locked_worktrees,
    )

    repo = source_repo.resolve()
    count = 0
    for entry in list_locked_worktrees(repo):
        if entry.parsed is None or entry.exists:
            continue
        key = f"{entry.parsed.dispatch_id}:{entry.path}"
        if key in _vanished_reported:
            continue
        _vanished_reported.add(key)
        ledger_status = "none"
        with ledger_connection() as conn:
            row = conn.execute(
                "SELECT status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (entry.parsed.dispatch_id,),
            ).fetchone()
            if row is not None and row["status"] is not None:
                ledger_status = str(row["status"])
        bridge_pid = worktree_held_by_live_bridge(
            worktree_path=entry.path,
            fresh=True,
        )
        emit_sdk_lane_b_pinned_worktree_vanished(
            dispatch_id=entry.parsed.dispatch_id,
            thread_id=entry.parsed.thread_id,
            worktree_path=str(entry.path),
            ledger_status=ledger_status,
            bridge_pid=bridge_pid,
        )
        count += 1
        if ledger_status not in ("completed", "failed", "cancelled") and bridge_pid is None:
            try:
                from services.git_integration_worker.cursor_sdk_worktree_remint import (
                    remint_lane_worktree,
                )
            except ImportError:
                remint_lane_worktree = None  # type: ignore[misc, assignment]
            if remint_lane_worktree is not None:
                remint_lane_worktree(
                    source_repo=repo,
                    dispatch_id=entry.parsed.dispatch_id,
                    thread_id=entry.parsed.thread_id,
                )
    return count


def reconcile_unregistered_worktrees(
    *,
    source_repo: Path,
    worktree_root: Path,
    active: set[str] | None = None,
) -> tuple[int, int]:
    """Archive-and-remove clean unregistered trees; surface dirty ones as debt.

    ``active`` is advisory — the caller computes it from records that can lag.
    The live-bridge and live-ledger scans are run here as well, so a tree with
    a running bridge or a non-terminal ledger row survives this sweep even when
    the caller passed a stale active set or none at all (H4).

    Returns ``(reconciled, surfaced)``.
    """
    from services.git_integration_worker.cursor_sdk_branch_archive import (
        archive_branch,
    )
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_reap_skipped_live_bridge,
        emit_sdk_lane_b_reconcile_skipped_live_ledger,
    )
    from services.git_integration_worker.cursor_sdk_worktree_gc import (
        is_lane_b_reconcile_target,
        registered_branch_names,
    )
    from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
        live_bridge_worktree_paths,
        live_ledger_worktree_paths,
        worktree_held_by_live_bridge,
    )

    repo = source_repo.resolve()
    root = worktree_root.resolve()
    active_paths = set(active or ())
    held_paths = live_bridge_worktree_paths(worktree_root=root)
    ledger_paths = live_ledger_worktree_paths(worktree_root=root)
    active_paths |= ledger_paths
    registered_branches = registered_branch_names()
    registered_paths = _registered_worktree_paths()

    reconciled = 0
    surfaced = 0
    for entry in list_git_worktrees(source_repo=repo):
        if not entry.path.is_relative_to(root):
            continue
        if not is_lane_b_reconcile_target(branch=entry.branch, path=entry.path):
            continue
        resolved = str(entry.path.resolve())
        if resolved in held_paths:
            logger.warning(
                "unregistered worktree left in place — live bridge holds it path=%s",
                entry.path,
            )
            emit_sdk_lane_b_reap_skipped_live_bridge(
                worktree_path=resolved,
                stage="reconcile",
            )
            continue
        if resolved in ledger_paths and resolved not in held_paths:
            logger.warning(
                "unregistered worktree left in place — live ledger holds it path=%s",
                entry.path,
            )
            emit_sdk_lane_b_reconcile_skipped_live_ledger(worktree_path=resolved)
            continue
        if resolved in active_paths or resolved in registered_paths:
            continue
        if entry.branch and entry.branch in registered_branches:
            continue
        if entry.path.is_dir() and _is_dirty(entry.path):
            surfaced += _surface_dirty_tree(entry)
            continue
        holder_pid = worktree_held_by_live_bridge(
            worktree_path=entry.path,
            worktree_root=root,
            fresh=True,
        )
        if holder_pid is not None:
            emit_sdk_lane_b_reap_skipped_live_bridge(
                worktree_path=resolved,
                pid=holder_pid,
                stage="reconcile",
            )
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
        thread_id="(unregistered-worktree)",
        dispatch_id=f"unregistered:{entry.path.name}",
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
    from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
        ledger_connection,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        ensure_worktree_schema,
    )

    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT worktree_path FROM cursor_sdk_lane_worktrees"
        ).fetchall()
    return {str(Path(row["worktree_path"]).resolve()) for row in rows}
