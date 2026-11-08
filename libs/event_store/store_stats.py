"""Cheap SQLite aggregates for observability (split subqueries, no seq span scans)."""

from __future__ import annotations

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


def hot_tier_health(store: EventStore) -> dict[str, object] | None:
    """Cheap hot-tier stats for /health when the tier is enabled."""
    return store.hot_tier_stats()


def estimate_event_count(bounds: dict[str, int | None]) -> int:
    """Upper-bound row count from seq bounds; avoids a COUNT(*) full scan.

    Retention deletes leave gaps, so this can exceed the true count.
    """
    max_seq = bounds.get("max_seq")
    min_seq = bounds.get("min_seq")
    if max_seq is None or min_seq is None:
        return 0
    return max_seq - min_seq + 1
