"""Edge presence stamps that ``admit`` reads for ``edge_unavailable``."""

from __future__ import annotations

import json
import sqlite3


def record_poll_sync(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    kinds: tuple[str, ...],
    now: float,
) -> None:
    """Stamp this edge's presence. ``admit`` reads it for ``edge_unavailable``."""
    connection.execute(
        "INSERT INTO edge_polls (edge_id, last_poll_epoch, kinds_json) "
        "VALUES (?, ?, ?) ON CONFLICT(edge_id) DO UPDATE SET "
        "last_poll_epoch = excluded.last_poll_epoch, "
        "kinds_json = excluded.kinds_json",
        (edge_id, now, json.dumps(list(kinds))),
    )
    connection.commit()


def edge_seen_since_sync(
    connection: sqlite3.Connection,
    since: float,
    *,
    kind: str,
) -> bool:
    """True when an edge serving *kind* has polled at or after *since*."""
    rows = connection.execute(
        "SELECT kinds_json FROM edge_polls WHERE last_poll_epoch >= ?",
        (since,),
    ).fetchall()
    return any(kind in json.loads(row["kinds_json"]) for row in rows)
