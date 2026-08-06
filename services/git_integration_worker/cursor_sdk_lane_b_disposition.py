"""Lane-B salvage branch disposition markers (F1 mark-and-reap)."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from services.git_integration_worker.cursor_dispatch_ledger import _connect

_BRANCH_SAFE = re.compile(r"[^A-Za-z0-9._/-]+")

_DISPOSITION_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_lane_b_branch_dispositions (
    branch_name   TEXT PRIMARY KEY,
    reason        TEXT NOT NULL,
    dispatch_id   TEXT NOT NULL,
    session_id    TEXT,
    tip_sha       TEXT,
    marked_at     TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class BranchDisposition:
    """Registry-side dispose marker for an orphan ``cursor-sdk/*`` branch."""

    branch_name: str
    reason: str
    dispatch_id: str
    session_id: str | None
    tip_sha: str | None
    marked_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_disposition_schema(conn: sqlite3.Connection) -> None:
    """Create disposition table when missing."""
    conn.executescript(_DISPOSITION_DDL)


def upsert_disposition(
    *,
    branch_name: str,
    reason: str,
    dispatch_id: str,
    session_id: str | None = None,
    tip_sha: str | None = None,
) -> BranchDisposition:
    """Insert or replace a branch disposition marker."""
    marked_at = _now()
    with _connect() as conn:
        ensure_disposition_schema(conn)
        conn.execute(
            "INSERT OR REPLACE INTO cursor_sdk_lane_b_branch_dispositions "
            "(branch_name, reason, dispatch_id, session_id, tip_sha, marked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (branch_name, reason, dispatch_id, session_id, tip_sha, marked_at),
        )
    return BranchDisposition(
        branch_name=branch_name,
        reason=reason,
        dispatch_id=dispatch_id,
        session_id=session_id,
        tip_sha=tip_sha,
        marked_at=marked_at,
    )


def get_disposition(*, branch_name: str) -> BranchDisposition | None:
    """Return the disposition row for *branch_name*, if any."""
    with _connect() as conn:
        ensure_disposition_schema(conn)
        row = conn.execute(
            "SELECT branch_name, reason, dispatch_id, session_id, tip_sha, marked_at "
            "FROM cursor_sdk_lane_b_branch_dispositions WHERE branch_name=?",
            (branch_name,),
        ).fetchone()
    if row is None:
        return None
    return BranchDisposition(
        branch_name=row["branch_name"],
        reason=row["reason"],
        dispatch_id=row["dispatch_id"],
        session_id=row["session_id"],
        tip_sha=row["tip_sha"],
        marked_at=row["marked_at"],
    )


def clear_disposition(*, branch_name: str) -> None:
    """Remove a disposition marker after reap or stale-marker resurrection."""
    with _connect() as conn:
        ensure_disposition_schema(conn)
        conn.execute(
            "DELETE FROM cursor_sdk_lane_b_branch_dispositions WHERE branch_name=?",
            (branch_name,),
        )


def list_dispositions() -> list[BranchDisposition]:
    """Return all disposition rows (tests and diagnostics)."""
    with _connect() as conn:
        ensure_disposition_schema(conn)
        rows = conn.execute(
            "SELECT branch_name, reason, dispatch_id, session_id, tip_sha, marked_at "
            "FROM cursor_sdk_lane_b_branch_dispositions ORDER BY marked_at"
        ).fetchall()
    return [
        BranchDisposition(
            branch_name=row["branch_name"],
            reason=row["reason"],
            dispatch_id=row["dispatch_id"],
            session_id=row["session_id"],
            tip_sha=row["tip_sha"],
            marked_at=row["marked_at"],
        )
        for row in rows
    ]


def mark_lane_b_disposition(
    *,
    branch_name: str,
    reason: str,
    dispatch_id: str,
    session_id: str | None = None,
    tip_sha: str | None = None,
) -> BranchDisposition:
    """Upsert a disposition marker and emit ``sdk.lane_b.disposition_marked``."""
    from services.git_integration_worker.cursor_sdk_events import (
        emit_sdk_lane_b_disposition_marked,
    )

    record = upsert_disposition(
        branch_name=branch_name,
        reason=reason,
        dispatch_id=dispatch_id,
        session_id=session_id,
        tip_sha=tip_sha,
    )
    emit_sdk_lane_b_disposition_marked(
        branch=branch_name,
        reason=reason,
        dispatch_id=dispatch_id,
        tip_sha=tip_sha,
    )
    return record


def branch_name_for_dispatch(dispatch_id: str) -> str:
    """Derive the minted ``cursor-sdk/*`` branch name for a dispatch id."""
    safe = _BRANCH_SAFE.sub("-", dispatch_id).strip("-") or "dispatch"
    return f"cursor-sdk/{safe}"


def mark_lane_b_disposition_for_dispatch(
    *,
    dispatch_id: str,
    source_repo: Path,
    reason: str,
    session_id: str | None = None,
) -> BranchDisposition | None:
    """Mark disposition when a dispatch abandons unlanded salvage work."""
    from services.git_integration_worker.cursor_sdk_lane_b_commit import (
        branch_state,
        orphan_branch_state,
    )
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_dispatch_worktree,
    )

    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    branch_name = record.branch_name if record is not None else branch_name_for_dispatch(
        dispatch_id
    )
    repo = source_repo.resolve()
    if record is not None:
        state = branch_state(
            repo,
            branch_name=record.branch_name,
            branch_point=record.branch_point,
        )
    else:
        state = orphan_branch_state(repo, branch_name=branch_name)
    if state.safe_to_delete:
        return None
    return mark_lane_b_disposition(
        branch_name=branch_name,
        reason=reason,
        dispatch_id=dispatch_id,
        session_id=session_id,
        tip_sha=state.head_sha,
    )
