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


__all__ = [
    "lookup_cse_nest_inherit_lane_thread_id",
    "lookup_nest_parent_lease_key",
]
