"""Reads and writes against ``edge_op_directives``.

At most one row per ``(op_id, type)`` is enforced by
``idx_edge_op_directives_op_type`` so concurrent folds cannot insert duplicate
``cancel`` directives.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def directives_after_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    after: int,
) -> list[dict[str, Any]]:
    """Return stored directives with ``seq`` greater than *after*."""
    rows = connection.execute(
        "SELECT seq, type, payload_json, at_epoch FROM edge_op_directives "
        "WHERE op_id = ? AND seq > ? ORDER BY seq ASC",
        (op_id, after),
    ).fetchall()
    return [
        {
            "seq": int(row["seq"]),
            "type": row["type"],
            "payload": json.loads(row["payload_json"]),
            "at_epoch": row["at_epoch"],
        }
        for row in rows
    ]


def write_cancel_directive_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    now: float,
) -> bool:
    """Write one ``cancel`` directive when none exists yet. True when inserted."""
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM edge_op_directives "
        "WHERE op_id = ?",
        (op_id,),
    ).fetchone()
    seq = int(row["next_seq"])
    cursor = connection.execute(
        "INSERT OR IGNORE INTO edge_op_directives "
        "(op_id, seq, type, payload_json, at_epoch) "
        "VALUES (?, ?, 'cancel', '{}', ?)",
        (op_id, seq, now),
    )
    return cursor.rowcount > 0
