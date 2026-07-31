"""Drain active consult_queue rows when a charter root closes (a:27395)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from libs.charter_runner_store.db import execute_with_retry

_DRAINABLE_STATUSES = ("queued", "admitted", "running")
_STATUS_PLACEHOLDERS = ",".join("?" for _ in _DRAINABLE_STATUSES)


@dataclass(frozen=True)
class DrainedConsultRow:
    queue_id: int
    root_id: str
    gid: str
    consult_role: str
    prior_status: str


def drain_consult_queue_for_root(
    conn,
    root_id: str,
    *,
    reason: str,
) -> list[DrainedConsultRow]:
    """Cancel active consult_queue rows for ``root_id`` (idempotent)."""
    rows = conn.execute(
        f"""
        SELECT id, root_id, gid, consult_role, status
        FROM consult_queue
        WHERE root_id = ? AND status IN ({_STATUS_PLACEHOLDERS})
        """,
        (root_id, *_DRAINABLE_STATUSES),
    ).fetchall()
    if not rows:
        return []

    now = time.time()
    drained: list[DrainedConsultRow] = []
    for row in rows:
        execute_with_retry(
            conn,
            f"""
            UPDATE consult_queue
               SET status = 'cancelled',
                   next_retry = NULL,
                   updated_at = ?
             WHERE id = ? AND status IN ({_STATUS_PLACEHOLDERS})
            """,
            (now, row["id"], *_DRAINABLE_STATUSES),
        )
        drained.append(
            DrainedConsultRow(
                queue_id=int(row["id"]),
                root_id=str(row["root_id"]),
                gid=str(row["gid"]),
                consult_role=str(row["consult_role"]),
                prior_status=str(row["status"]),
            )
        )
    return drained


def drain_orphan_consults_under_closed_roots(
    conn,
    *,
    reason: str,
) -> list[DrainedConsultRow]:
    """One-shot: cancel active consult rows whose root is already CLOSED."""
    rows = conn.execute(
        f"""
        SELECT cq.id, cq.root_id, cq.gid, cq.consult_role, cq.status
        FROM consult_queue cq
        JOIN root_ledger rl ON rl.root_id = cq.root_id
        WHERE cq.status IN ({_STATUS_PLACEHOLDERS})
          AND rl.status = 'CLOSED'
        ORDER BY cq.root_id, cq.id
        """,
        _DRAINABLE_STATUSES,
    ).fetchall()
    drained: list[DrainedConsultRow] = []
    seen_roots: set[str] = set()
    for row in rows:
        root_id = str(row["root_id"])
        if root_id in seen_roots:
            continue
        seen_roots.add(root_id)
        drained.extend(drain_consult_queue_for_root(conn, root_id, reason=reason))
    return drained


__all__ = [
    "DrainedConsultRow",
    "drain_consult_queue_for_root",
    "drain_orphan_consults_under_closed_roots",
]
