"""Whether a Lane-B thread still has a successor that must keep the tree."""

from __future__ import annotations

import sqlite3

from services.git_integration_worker.cursor_dispatch_ledger import _connect


def thread_has_inheritor(
    thread_id: str,
    *,
    completing_dispatch_id: str | None = None,
) -> bool:
    """True when another Auto job or SDK dispatch still owns ``thread_id``.

    The completing Auto job is still ``claimed`` at closeout; one live Auto
    job is not an inheritor. Two or more queued/claimed Auto jobs are.
    A different admitted/running SDK dispatch on the same thread is.
    """
    if not thread_id.strip():
        return False
    if _live_auto_count(thread_id) >= 2:
        return True
    return _other_live_sdk_dispatch(
        thread_id, completing_dispatch_id=completing_dispatch_id
    )


def _live_auto_count(thread_id: str) -> int:
    from services.git_integration_worker.cursor_auto.queue import get_queue

    return sum(
        1
        for job in get_queue().list_open_jobs()
        if job.thread_id == thread_id and job.status in {"queued", "claimed"}
    )


def _other_live_sdk_dispatch(
    thread_id: str,
    *,
    completing_dispatch_id: str | None,
) -> bool:
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE thread_id=? AND COALESCE(read_only,0)=0 "
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
