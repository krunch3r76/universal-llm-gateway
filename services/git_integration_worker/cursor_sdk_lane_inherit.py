"""Whether a Lane-B thread still has a successor that must keep the tree."""

from __future__ import annotations

import sqlite3

from services.git_integration_worker.cursor_sdk_worktree_live_guard import (
    ledger_connection,
)


def thread_has_inheritor(
    thread_id: str,
    *,
    completing_dispatch_id: str | None = None,
) -> bool:
    """True when another SDK dispatch still owns ``thread_id``.

    A different admitted/running SDK dispatch on the same thread is an
    inheritor — including ``read_only=1`` dispatches standing in the lane
    worktree.
    """
    if not thread_id.strip():
        return False
    return _other_live_sdk_dispatch(
        thread_id, completing_dispatch_id=completing_dispatch_id
    )


def _other_live_sdk_dispatch(
    thread_id: str,
    *,
    completing_dispatch_id: str | None,
) -> bool:
    try:
        with ledger_connection() as conn:
            rows = conn.execute(
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE thread_id=? "
                "AND status IN ('admitted','running')",
                (thread_id,),
            ).fetchall()
    except sqlite3.OperationalError:
        return False
    for row in rows:
        dispatch_id = str(row["dispatch_id"] or "")
        if completing_dispatch_id and dispatch_id == completing_dispatch_id:
            continue
        if dispatch_id:
            return True
    return False


__all__ = ["thread_has_inheritor"]
