"""Idempotency-key lookup for ``create_thread`` retries (a:36915).

A client-side timeout on ``POST /threads`` can hide a committed insert; the
retry then mints a sibling (specimen: thread 13512, 2026-09-29). When the
caller sends ``idempotency_key``, these helpers resolve the key to the thread
the first call created so ``create_thread`` returns it instead.

Turn 1 itself is not waited on. ``committed_first_turn`` is a non-blocking
read. The insert path claims ``turn_number=1`` inside the same
``write_connect`` as the existence check (``UNIQUE(thread, turn_number)``),
so a failed first send can still insert and a concurrent replay returns the
committed row without polling.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .connection import connect
from .threads import get_thread_with_links


def thread_id_for_idempotency_key(conn: sqlite3.Connection, key: str) -> str | None:
    """Return the thread id already bound to ``key``, or None when the key is free.

    Caller must hold ``conn`` (mint uses the write transaction). Does not insert
    a thread and does not look at turn 1.
    """
    row = conn.execute(
        "SELECT id FROM threads WHERE idempotency_key = ? LIMIT 1", (key,)
    ).fetchone()
    return None if row is None else str(row["id"])


def replayed_thread_detail(thread_id: str) -> dict[str, Any]:
    """Existing thread detail, flagged so the route can answer 200 not 201."""
    detail = get_thread_with_links(thread_id)
    if detail is None:
        raise RuntimeError(
            f"idempotency_key resolved to thread {thread_id} but it is not readable"
        )
    detail["idempotent_replay"] = True
    return detail


def _turn1_row(conn: sqlite3.Connection, thread_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM turns WHERE thread = ? AND turn_number = 1 LIMIT 1",
        (thread_id,),
    ).fetchone()
    return None if row is None else dict(row)


def committed_first_turn(thread_id: str) -> dict[str, Any] | None:
    """Return turn 1 when it is already committed.

    Read-only: no write ticket and no wait. A miss means the caller may claim
    turn 1 inside ``insert_turn(..., idempotent_first_turn=True)``. Callers
    that need the row a peer is inserting must take that claim path; this
    helper does not poll.
    """
    with connect() as conn:
        return _turn1_row(conn, thread_id)
