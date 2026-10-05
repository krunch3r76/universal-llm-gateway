"""Non-terminal dispatch journal transitions (started fold + append log).

Terminal writes remain in ``dispatch_journal``; this module owns the forward
migration, ``started`` rows, and ``fetch_record`` for any status.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_TRANSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS dispatch_record_transitions (
    execution_id TEXT NOT NULL,
    status       TEXT NOT NULL,
    at           TEXT NOT NULL,
    at_epoch     REAL NOT NULL
);
"""

_NEW_TABLE_SQL = """
CREATE TABLE dispatch_records (
    execution_id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started','completed','failed')),
    caller_agent TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    completed_at_epoch REAL,
    updated_at TEXT NOT NULL,
    updated_at_epoch REAL NOT NULL,
    record_json TEXT NOT NULL
);
"""

_INDEX_TRANSITIONS = """
CREATE INDEX IF NOT EXISTS idx_dispatch_record_transitions_execution_id
    ON dispatch_record_transitions(execution_id);
"""

_INDEX_UPDATED = """
CREATE INDEX IF NOT EXISTS idx_dispatch_records_updated_at_epoch
    ON dispatch_records(updated_at_epoch);
"""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def migrate_schema_sync(connection: sqlite3.Connection) -> None:
    """Idempotent schema migration. Old terminal-only dispatch records become a fold table plus a transition log."""
    connection.execute(_TRANSITIONS_DDL)
    connection.execute(_INDEX_TRANSITIONS)
    if not _table_columns(connection, "dispatch_records"):
        connection.executescript(
            _NEW_TABLE_SQL
            + _INDEX_UPDATED
            + """
CREATE INDEX IF NOT EXISTS idx_dispatch_records_completed_at
    ON dispatch_records(completed_at_epoch);
"""
        )
        connection.commit()
        return
    cols = _table_columns(connection, "dispatch_records")
    if "updated_at" in cols:
        connection.execute(_INDEX_UPDATED)
        connection.commit()
        return
    connection.execute("ALTER TABLE dispatch_records RENAME TO dispatch_records_old")
    connection.executescript(_NEW_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO dispatch_records(
            execution_id, pipeline, status, caller_agent,
            started_at, completed_at, completed_at_epoch,
            updated_at, updated_at_epoch, record_json
        )
        SELECT execution_id, pipeline, status, caller_agent,
            started_at, completed_at, completed_at_epoch,
            completed_at, completed_at_epoch, record_json
        FROM dispatch_records_old
        """
    )
    connection.execute("DROP TABLE dispatch_records_old")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dispatch_records_completed_at
            ON dispatch_records(completed_at_epoch);
        """
    )
    connection.execute(_INDEX_UPDATED)
    connection.commit()
    logger.info("dispatch_records schema migrated for started transitions")


def _iso_epoch(iso_ts: str) -> float:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()


def write_transition_sync(
    path: Path,
    *,
    execution_id: str,
    pipeline: str,
    status: str,
    caller_agent: str | None,
    started_at: str,
    completed_at: str | None,
    record_json: dict[str, Any],
) -> int:
    """Upsert fold row and append one transition line; return JSON byte size."""
    now_iso = datetime.now().astimezone().isoformat().replace("+00:00", "Z")
    now_epoch = time.time()
    at_iso = completed_at if status != "started" and completed_at else started_at
    at_epoch = _iso_epoch(at_iso)
    completed_epoch = (
        _iso_epoch(completed_at) if completed_at is not None else None
    )
    payload_json = json.dumps(record_json, separators=(",", ":"), ensure_ascii=True)
    terminal_statuses = frozenset({"completed", "failed"})
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL;")
        migrate_schema_sync(connection)
        existing = connection.execute(
            "SELECT status FROM dispatch_records WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if status == "started":
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO dispatch_records(
                        execution_id, pipeline, status, caller_agent,
                        started_at, completed_at, completed_at_epoch,
                        updated_at, updated_at_epoch, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution_id,
                        pipeline,
                        status,
                        caller_agent,
                        started_at,
                        completed_at,
                        completed_epoch,
                        now_iso,
                        now_epoch,
                        payload_json,
                    ),
                )
            elif str(existing[0]) not in terminal_statuses:
                connection.execute(
                    """
                    UPDATE dispatch_records SET
                        pipeline = ?, status = ?, caller_agent = ?,
                        started_at = ?, updated_at = ?, updated_at_epoch = ?,
                        record_json = ?
                    WHERE execution_id = ?
                    """,
                    (
                        pipeline,
                        status,
                        caller_agent,
                        started_at,
                        now_iso,
                        now_epoch,
                        payload_json,
                        execution_id,
                    ),
                )
        else:
            connection.execute(
                """
                INSERT OR REPLACE INTO dispatch_records(
                    execution_id, pipeline, status, caller_agent,
                    started_at, completed_at, completed_at_epoch,
                    updated_at, updated_at_epoch, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    pipeline,
                    status,
                    caller_agent,
                    started_at,
                    completed_at,
                    completed_epoch,
                    now_iso,
                    now_epoch,
                    payload_json,
                ),
            )
        connection.execute(
            """
            INSERT INTO dispatch_record_transitions(
                execution_id, status, at, at_epoch
            ) VALUES (?, ?, ?, ?)
            """,
            (execution_id, status, at_iso, at_epoch),
        )
        connection.commit()
    return len(payload_json.encode("utf-8"))


def fetch_record_sync(
    path: Path,
    execution_id: str,
) -> tuple[dict[str, Any], str, float] | None:
    """Return ``(record_json, status, updated_at_epoch)`` for any status, or None if the id is missing."""
    with sqlite3.connect(path) as connection:
        migrate_schema_sync(connection)
        row = connection.execute(
            """
            SELECT record_json, status, updated_at_epoch
            FROM dispatch_records
            WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    payload_json, status, updated_epoch = row
    return json.loads(payload_json), str(status), float(updated_epoch)


def prune_started_sync(
    path: Path,
    retention_seconds: float,
) -> int:
    """Delete ``started`` rows whose ``updated_at_epoch`` is older than now minus *retention_seconds*."""
    cutoff = time.time() - retention_seconds
    with sqlite3.connect(path) as connection:
        migrate_schema_sync(connection)
        deleted = connection.execute(
            """
            DELETE FROM dispatch_records
            WHERE status = 'started' AND updated_at_epoch < ?
            """,
            (cutoff,),
        ).rowcount
        connection.commit()
    return max(0, deleted)
