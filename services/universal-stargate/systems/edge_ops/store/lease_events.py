"""Append-only edge event rows.

``edge_op_events`` primary key is ``(op_id, seq)``; ``append_event_sync`` retries
on ``IntegrityError`` when two writers race on ``MAX(seq)+1``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_MAX_SEQ_RETRIES = 8


def append_event_sync(
    connection: sqlite3.Connection,
    op_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: float,
) -> int:
    """Append an open-typed edge event and return its sequence number."""
    payload_json = json.dumps(payload, sort_keys=True)
    for _ in range(_MAX_SEQ_RETRIES):
        row = connection.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM edge_op_events "
            "WHERE op_id = ?",
            (op_id,),
        ).fetchone()
        seq = int(row["next_seq"])
        try:
            connection.execute(
                "INSERT INTO edge_op_events "
                "(op_id, seq, type, payload_json, at_epoch) "
                "VALUES (?, ?, ?, ?, ?)",
                (op_id, seq, event_type, payload_json, now),
            )
        except sqlite3.IntegrityError:
            continue
        return seq
    raise sqlite3.IntegrityError(f"could not allocate event seq for {op_id!r}")
