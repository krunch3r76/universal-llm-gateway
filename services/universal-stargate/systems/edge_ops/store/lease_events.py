"""Append-only edge event rows."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def append_event_sync(
    connection: sqlite3.Connection,
    op_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: float,
) -> int:
    """Append an open-typed edge event and return its sequence number."""
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM edge_op_events "
        "WHERE op_id = ?",
        (op_id,),
    ).fetchone()
    seq = int(row["next_seq"])
    connection.execute(
        "INSERT INTO edge_op_events (op_id, seq, type, payload_json, at_epoch) "
        "VALUES (?, ?, ?, ?, ?)",
        (op_id, seq, event_type, json.dumps(payload, sort_keys=True), now),
    )
    return seq
