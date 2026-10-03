"""Admit-transaction gate: one ``resume_of`` child per await-parked lineage.

``CursorDispatchLedger.admit`` calls ``refuse_if_await_resume_taken`` inside
its ``BEGIN IMMEDIATE`` so a hand ``resume_of`` and the GIW reactor cannot both
mint a child for a row parked on outstanding CDP replies (friction 34156). The
loser gets ``ResumeAlreadyAdmitted`` naming the child that already exists;
the route maps it to a 409. Kept out of ``cursor_dispatch_ledger`` (far past
its SLOC ceiling) the way the other work-key gates are.
"""

from __future__ import annotations

import sqlite3

PARK_KIND_AWAIT_REPLY = "await_cdp_reply"
_LIVE_CHILD_STATUSES = "('queued','admitted','running','parked_waiting')"
# Reconcile / gate: live children plus completed (ran to terminal). A child that
# failed before started_at was set (drain 503 / nest-transfer after ledger.admit)
# must not close the park or block a later resume (friction 34156 F2).
RECONCILE_CHILD_STATUSES_SQL = (
    "('queued','admitted','running','parked_waiting','completed')"
)


class ResumeAlreadyAdmitted(Exception):  # noqa: N818 — admit gate, sibling of SourceRefConflict
    """A ``resume_of`` child already exists for this await-parked parent."""

    def __init__(
        self,
        *,
        parent_dispatch_id: str,
        existing_child_id: str,
        thread_id: str | None,
    ) -> None:
        self.parent_dispatch_id = parent_dispatch_id
        self.existing_child_id = existing_child_id
        self.thread_id = thread_id
        super().__init__(
            f"dispatch {parent_dispatch_id!r} was already resumed by "
            f"{existing_child_id!r} (thread {thread_id!r}); one resume per "
            "awaited-reply lineage"
        )


def _failed_before_run(conn: sqlite3.Connection, child_id: str) -> bool:
    """True when *child_id* is missing or failed without ever starting."""
    row = conn.execute(
        "SELECT status, started_at FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (child_id,),
    ).fetchone()
    if row is None:
        return True
    return str(row["status"] or "") == "failed" and row["started_at"] is None


def refuse_if_await_resume_taken(
    conn: sqlite3.Connection, *, parent_id: str, child_id: str
) -> None:
    """Raise when *parent_id* is an await-park whose resume is already taken.

    Two witnesses close the lineage: ``park_resumed_by`` (stamped by a hand
    admit in-transaction or by the reactor after route success) and a live
    child row with ``resume_of=parent`` (covers the reactor's admit-before-
    stamp window). A stamped child that failed before it ran is ignored so a
    later resume can recover. Other park kinds are untouched.
    """
    parent = conn.execute(
        "SELECT park_kind, park_resumed_by, thread_id FROM cursor_sdk_dispatches "
        "WHERE dispatch_id=?",
        (parent_id,),
    ).fetchone()
    if parent is None or parent["park_kind"] != PARK_KIND_AWAIT_REPLY:
        return
    stamped = parent["park_resumed_by"]
    if stamped and stamped != child_id and not _failed_before_run(conn, str(stamped)):
        raise ResumeAlreadyAdmitted(
            parent_dispatch_id=parent_id,
            existing_child_id=str(stamped),
            thread_id=parent["thread_id"],
        )
    live = conn.execute(
        "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE resume_of=? "
        f"AND dispatch_id<>? AND status IN {_LIVE_CHILD_STATUSES} "
        "ORDER BY rowid LIMIT 1",
        (parent_id, child_id),
    ).fetchone()
    if live is not None:
        raise ResumeAlreadyAdmitted(
            parent_dispatch_id=parent_id,
            existing_child_id=str(live["dispatch_id"]),
            thread_id=parent["thread_id"],
        )


__all__ = [
    "PARK_KIND_AWAIT_REPLY",
    "RECONCILE_CHILD_STATUSES_SQL",
    "ResumeAlreadyAdmitted",
    "refuse_if_await_resume_taken",
]
