"""Resolve write-lease keys for ``nest_under`` parents (SDK dispatch or ``cse:``)."""

from __future__ import annotations

from pathlib import Path

from claude_bundles.holder_strings import parse_nest_under_cse

from services.git_integration_worker.cse_session_holders import resolve_nest_parent
from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_lane_worktree,
)


def _lookup_sdk_dispatch_parent_lease_key(parent_id: str) -> str | None:
    with ledger_connection() as conn:
        row = conn.execute(
            "SELECT lease_key, source_repo FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
    if row is None:
        return None
    return row["lease_key"] or row["source_repo"]


def _lookup_cse_nest_parent_lease_key(
    holder_id: str,
    *,
    source_repo: Path,
) -> str | None:
    with ledger_connection() as conn:
        parent = resolve_nest_parent(conn, holder_id)
    if parent is None:
        return None
    lane_thread_id = str(parent.get("lane_thread_id") or "").strip()
    if lane_thread_id:
        record = lookup_lane_worktree(
            thread_id=lane_thread_id,
            source_repo=source_repo,
        )
        if record is not None and record.worktree_path.is_dir():
            return str(record.worktree_path.resolve())
    return str(source_repo.resolve())


def lookup_nest_parent_lease_key(
    parent_id: str,
    *,
    source_repo: Path | None = None,
) -> str | None:
    """Return lease key for ``nest_under`` / ``resume_of`` parent resolution."""
    holder_id = parse_nest_under_cse(parent_id)
    if holder_id is not None:
        if source_repo is None:
            return None
        return _lookup_cse_nest_parent_lease_key(holder_id, source_repo=source_repo)
    return _lookup_sdk_dispatch_parent_lease_key(parent_id)


def lookup_cse_nest_inherit_lane_thread_id(nest_under: str) -> str | None:
    """Lane thread id whose pin a ``cse:`` nest child may inherit (not steal)."""
    holder_id = parse_nest_under_cse(nest_under)
    if holder_id is None:
        return None
    with ledger_connection() as conn:
        parent = resolve_nest_parent(conn, holder_id)
    if parent is None:
        return None
    lane = str(parent.get("lane_thread_id") or "").strip()
    return lane or None


# Resume chains are short (one -cN per attempt). The cap stops a cycle
# from walking the ledger forever; 16 is above the nest-depth limit.
_RESUME_INHERIT_WALK_MAX = 16


def lookup_resume_inherit_lane_thread_id(resume_of: str) -> str | None:
    """Lane thread whose pin a ``resume_of`` child may inherit.

    A nested child runs on its own worker thread while the nest parent
    (SDK dispatch or ``cse:`` holder) keeps the lane worktree lock.
    A later resume of that resume child has ``nest_under`` NULL, so the
    walk follows ``resume_of`` to the first row that still names the nest.
    Same-thread resumes return None so the ordinary relock path runs.
    The returned id is only the nest parent's thread, so an unrelated
    lock still fails.
    """
    current = resume_of.strip()
    seen: set[str] = set()
    nest_under = ""
    nested_thread = ""
    for _ in range(_RESUME_INHERIT_WALK_MAX):
        if not current or current in seen:
            return None
        seen.add(current)
        with ledger_connection() as conn:
            row = conn.execute(
                "SELECT thread_id, nest_under, resume_of "
                "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (current,),
            ).fetchone()
        if row is None:
            return None
        found = str(row["nest_under"] or "").strip()
        if found:
            nest_under = found
            nested_thread = str(row["thread_id"] or "").strip()
            break
        current = str(row["resume_of"] or "").strip()
    else:
        return None
    if not nest_under:
        return None
    holder = lookup_cse_nest_inherit_lane_thread_id(nest_under)
    if holder:
        lane = holder
    else:
        with ledger_connection() as conn:
            parent = conn.execute(
                "SELECT thread_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (nest_under,),
            ).fetchone()
        if parent is None:
            return None
        lane = str(parent["thread_id"] or "").strip()
    if not lane or lane == nested_thread:
        return None
    return lane


def inherit_lane_thread_id_for_admit(
    *,
    nest_under: str | None,
    resume_of: str | None,
) -> str | None:
    """Pin inherit id for a nest child or a resume of a nested child."""
    if nest_under:
        return lookup_cse_nest_inherit_lane_thread_id(nest_under)
    if resume_of:
        return lookup_resume_inherit_lane_thread_id(resume_of)
    return None


__all__ = [
    "inherit_lane_thread_id_for_admit",
    "lookup_cse_nest_inherit_lane_thread_id",
    "lookup_nest_parent_lease_key",
    "lookup_resume_inherit_lane_thread_id",
]
