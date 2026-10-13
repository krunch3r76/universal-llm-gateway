"""Migration 006: drain consult_queue orphans under already-CLOSED roots (a:27395)."""

from __future__ import annotations

import sqlite3
import time

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.006")

MIGRATION_ID = "migration_006_consult_orphan_drain"

_DRAINABLE_STATUSES = ("queued", "admitted", "running")
_STATUS_PLACEHOLDERS = ",".join("?" for _ in _DRAINABLE_STATUSES)


def _drain_orphan_consults_under_closed_roots(conn: sqlite3.Connection, *, reason: str) -> int:
    """Inline orphan drain (archived consult_drain; no package import)."""
    from ..db import execute_with_retry

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
    drained = 0
    seen_roots: set[str] = set()
    now = time.time()
    for row in rows:
        root_id = str(row["root_id"])
        if root_id in seen_roots:
            continue
        seen_roots.add(root_id)
        active = conn.execute(
            f"""
            SELECT id FROM consult_queue
            WHERE root_id = ? AND status IN ({_STATUS_PLACEHOLDERS})
            """,
            (root_id, *_DRAINABLE_STATUSES),
        ).fetchall()
        for active_row in active:
            execute_with_retry(
                conn,
                f"""
                UPDATE consult_queue
                   SET status = 'cancelled',
                       next_retry = NULL,
                       updated_at = ?
                 WHERE id = ? AND status IN ({_STATUS_PLACEHOLDERS})
                """,
                (now, active_row["id"], *_DRAINABLE_STATUSES),
            )
            drained += 1
    return drained


def migrate(conn: sqlite3.Connection) -> None:
    drained = _drain_orphan_consults_under_closed_roots(
        conn,
        reason="migration_006_orphan_cleanup",
    )
    conn.commit()
    logger.info(
        "migration 006: drained %d orphan consult_queue row(s)",
        drained,
    )
