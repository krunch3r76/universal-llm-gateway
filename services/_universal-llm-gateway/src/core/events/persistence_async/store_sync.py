"""Synchronous SQLite operations for AsyncEventStore thread-pool execution."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def init_database(db_path: Path) -> None:
    """Initialize database schema and indices."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp
            ON events(timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_type
            ON events(event_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at
            ON events(created_at DESC)
        """)
        conn.commit()


def store_event_sync(
    db_path: Path, timestamp: float, event_type: str, event_json: str
) -> None:
    """Insert one serialized event row into SQLite and commit the transaction."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (timestamp, event_type, event_data) VALUES (?, ?, ?)",
            (timestamp, event_type, event_json),
        )
        conn.commit()


def query_events_sync(
    db_path: Path,
    event_type: str | None,
    since: float | None,
    until: float | None,
    limit: int | None,
    offset: int,
) -> list[dict[str, Any]]:
    """Query persisted events with optional type and time filters, returning parsed payloads."""
    query = (
        "SELECT id, timestamp, event_type, event_data, created_at FROM events WHERE 1=1"
    )
    params: list[Any] = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    if since:
        query += " AND timestamp >= ?"
        params.append(since)

    if until:
        query += " AND timestamp <= ?"
        params.append(until)

    query += " ORDER BY timestamp DESC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    if offset:
        query += " OFFSET ?"
        params.append(offset)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(query, params)
        rows = cursor.fetchall()

    events = []
    for row in rows:
        event_dict = dict(row)
        event_dict["event_data"] = json.loads(event_dict["event_data"])
        events.append(event_dict)

    return events


def count_events_sync(
    db_path: Path,
    event_type: str | None,
    since: float | None,
    until: float | None,
) -> int:
    """Return the number of events matching optional type and timestamp filters."""
    query = "SELECT COUNT(*) FROM events WHERE 1=1"
    params: list[Any] = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)

    if since:
        query += " AND timestamp >= ?"
        params.append(since)

    if until:
        query += " AND timestamp <= ?"
        params.append(until)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(query, params)
        return cursor.fetchone()[0]


def get_event_types_sync(db_path: Path) -> list[str]:
    """Return distinct event_type values present in the events table, sorted alphabetically."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT DISTINCT event_type FROM events ORDER BY event_type"
        )
        return [row[0] for row in cursor.fetchall()]


def cleanup_old_events_sync(db_path: Path, max_age_days: int) -> int:
    """Delete events older than max_age_days and return the number of rows removed."""
    cutoff_time = time.time() - (max_age_days * 24 * 3600)

    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff_time,))
        deleted_count = cursor.rowcount
        conn.commit()

    if deleted_count > 0:
        logger.info(
            f"🗑️  Cleaned up {deleted_count} events older than {max_age_days} days"
        )

    return deleted_count


def get_event_count_sync(db_path: Path) -> int:
    """Return total row count in the events table without applying filters."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM events")
        return cursor.fetchone()[0]


def delete_oldest_events_sync(db_path: Path, to_delete: int) -> None:
    """Delete the oldest to_delete events by timestamp to enforce retention limits."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"""
            DELETE FROM events WHERE id IN (
                SELECT id FROM events ORDER BY timestamp ASC LIMIT {to_delete}
            )
        """)
        conn.commit()


def clear_all_events_sync(db_path: Path) -> int:
    """Delete every event row and return how many records were removed."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM events")
        deleted_count = cursor.rowcount
        conn.commit()

    logger.info(f"🗑️  Cleared all {deleted_count} events from database")
    return deleted_count


def get_stats_sync(db_path: Path) -> dict[str, Any]:
    """Return aggregate event-store statistics including counts, time span, and database size."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM events")
        total_count = cursor.fetchone()[0]

        cursor = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events")
        min_ts, max_ts = cursor.fetchone()

        cursor = conn.execute("""
            SELECT event_type, COUNT(*) as count
            FROM events
            GROUP BY event_type
            ORDER BY count DESC
        """)
        type_counts = {row[0]: row[1] for row in cursor.fetchall()}

        db_size_bytes = db_path.stat().st_size if db_path.exists() else 0

    return {
        "total_events": total_count,
        "oldest_timestamp": min_ts,
        "newest_timestamp": max_ts,
        "time_span_seconds": (max_ts - min_ts) if min_ts and max_ts else 0,
        "event_type_counts": type_counts,
        "database_size_bytes": db_size_bytes,
        "database_size_mb": db_size_bytes / (1024 * 1024),
    }
