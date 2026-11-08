"""Cheap SQLite aggregates for observability (split subqueries, no seq span scans)."""

from __future__ import annotations

from typing import Any

from .store import EventStore

_SEQ_BOUNDS_SQL = (
    "SELECT "
    "(SELECT MAX(seq) FROM events) AS max_seq, "
    "(SELECT MIN(seq) FROM events) AS min_seq"
)


async def query_seq_bounds(store: EventStore) -> dict[str, int | None]:
    """Return newest and oldest ``seq`` using independent index lookups."""
    rows = await store.query(_SEQ_BOUNDS_SQL, (), limit=1)
    if not rows:
        return {"max_seq": None, "min_seq": None}
    row = rows[0]
    max_seq = row.get("max_seq")
    min_seq = row.get("min_seq")
    return {
        "max_seq": int(max_seq) if max_seq is not None else None,
        "min_seq": int(min_seq) if min_seq is not None else None,
    }


async def query_event_count(store: EventStore) -> int:
    rows = await store.query("SELECT COUNT(*) AS count FROM events", (), limit=1)
    if not rows:
        return 0
    return int(rows[0].get("count") or 0)
