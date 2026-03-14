"""SQLite event store — schema, insert, query, retention.

WAL mode with PRAGMA synchronous=NORMAL for high-throughput writes.
Generated virtual columns promote correlation fields from JSON payload
for indexed O(log N) lookups without schema churn.

Invariant: SQLite is the sole authoritative store for all queries.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    signal TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'observation',
    scope TEXT NOT NULL DEFAULT 'global',
    ts_unix_ms INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    request_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.request_id')) VIRTUAL,
    execution_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.execution_id')) VIRTUAL,
    model_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.model_id')) VIRTUAL,
    gateway_id TEXT GENERATED ALWAYS AS (json_extract(payload, '$.gateway_id')) VIRTUAL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_signal_ts ON events(signal, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_request_id ON events(request_id) WHERE request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_execution_id ON events(execution_id) WHERE execution_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_role_scope_ts ON events(role, scope, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_source_ts ON events(source, ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_model_id ON events(model_id) WHERE model_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS request_snapshots (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    ts_unix_ms INTEGER NOT NULL,
    model_id TEXT,
    gateway_id TEXT,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_snap_request ON request_snapshots(request_id, phase);
CREATE INDEX IF NOT EXISTS idx_snap_ts ON request_snapshots(ts_unix_ms);
CREATE INDEX IF NOT EXISTS idx_snap_model ON request_snapshots(model_id) WHERE model_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    baseline_id TEXT,
    metrics TEXT,
    verdict TEXT,
    ts_unix_ms INTEGER NOT NULL
);
"""

_INSERT_EVENT = (
    "INSERT INTO events (event_id, signal, role, scope, ts_unix_ms, timestamp, source, payload) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

_INSERT_SNAPSHOT = (
    "INSERT INTO request_snapshots (request_id, phase, ts_unix_ms, model_id, gateway_id, payload) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_MAX_PAYLOAD_BYTES = 64 * 1024


def _ts_ms_from_iso(iso: str) -> int:
    """Convert ISO 8601 timestamp to Unix epoch milliseconds."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return int(time.time() * 1000)


class EventStore:
    """SQLite-backed event store with batched writes and retention."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open the database and initialize schema."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.executescript(_SCHEMA_SQL)
        await self._db.commit()
        logger.info("EventStore opened: %s", self._db_path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def insert_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert a batch of events. Returns the events with seq assigned.

        Skips events whose JSON payload exceeds 64KB. On DB error (e.g. disk
        full), logs and drops the batch to keep the service alive.
        """
        if not self._db or not events:
            return []

        rows: list[tuple[Any, ...]] = []
        accepted: list[dict[str, Any]] = []
        for ev in events:
            payload_str = json.dumps(ev.get("payload")) if ev.get("payload") else None
            if payload_str and len(payload_str.encode()) > _MAX_PAYLOAD_BYTES:
                logger.warning(
                    "Dropping oversized event: signal=%s size=%d",
                    ev.get("signal"),
                    len(payload_str.encode()),
                )
                continue
            ts_iso = ev.get("timestamp", "")
            ts_ms = ev.get("ts_unix_ms") or (
                _ts_ms_from_iso(ts_iso) if ts_iso else int(time.time() * 1000)
            )
            rows.append(
                (
                    ev.get("id"),
                    ev.get("signal", "unknown"),
                    ev.get("role", "observation"),
                    ev.get("scope", "global"),
                    ts_ms,
                    ts_iso,
                    ev.get("source", "unknown"),
                    payload_str,
                )
            )
            accepted.append(ev)

        if not rows:
            return []

        try:
            await self._db.executemany(_INSERT_EVENT, rows)
            await self._db.commit()
        except Exception as e:
            logger.error(
                "DB write failed, dropping %d events (signals: %s): %s",
                len(rows),
                [ev.get("signal") for ev in accepted[:5]],
                e,
            )
            return []

        return accepted

    async def insert_snapshot(self, snap: dict[str, Any]) -> None:
        """Insert a request snapshot record."""
        if not self._db:
            return
        payload_str = json.dumps(snap.get("payload")) if snap.get("payload") else None
        try:
            await self._db.execute(
                _INSERT_SNAPSHOT,
                (
                    snap.get("request_id", ""),
                    snap.get("phase", ""),
                    snap.get("ts_unix_ms", int(time.time() * 1000)),
                    snap.get("model_id"),
                    snap.get("gateway_id"),
                    payload_str,
                ),
            )
            await self._db.commit()
        except Exception as e:
            logger.error(
                "Snapshot insert failed request_id=%s phase=%s: %s",
                snap.get("request_id"),
                snap.get("phase"),
                e,
            )

    async def query(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Execute a read query and return rows as dicts."""
        if not self._db:
            return []
        try:
            cursor = await self._db.execute(sql, params)
            raw_rows = await cursor.fetchmany(limit)
            return [dict(r) for r in raw_rows]
        except Exception as e:
            logger.error("Query failed: %s params=%s — %s", sql[:120], params, e)
            return []

    async def run_retention(self, max_age_ms: int) -> int:
        """Delete rows older than max_age_ms from all retained tables.

        Returns total count deleted across events, request_snapshots, and
        evaluations.
        """
        if not self._db:
            return 0
        cutoff = int(time.time() * 1000) - max_age_ms
        try:
            r1 = await self._db.execute(
                "DELETE FROM events WHERE ts_unix_ms < ?", (cutoff,)
            )
            r2 = await self._db.execute(
                "DELETE FROM request_snapshots WHERE ts_unix_ms < ?", (cutoff,)
            )
            r3 = await self._db.execute(
                "DELETE FROM evaluations WHERE ts_unix_ms < ?", (cutoff,)
            )
            await self._db.execute("PRAGMA incremental_vacuum")
            await self._db.commit()
            return max(r1.rowcount or 0, 0) + max(r2.rowcount or 0, 0) + max(r3.rowcount or 0, 0)
        except Exception as e:
            logger.error("Retention failed cutoff=%s: %s", cutoff, e)
            return 0

    async def run_session_retention(self, max_sessions: int) -> int:
        """Delete rows older than the Nth most recent system.started boundary.

        For max_sessions=2 this keeps rows from the two most recent Stargate
        sessions. Uses OFFSET max_sessions - 1 to identify the oldest boundary
        that should remain, then deletes older rows across all retained tables.
        """
        if not self._db or max_sessions < 1:
            return 0

        rows = await self.query(
            "SELECT ts_unix_ms FROM events WHERE signal = 'system.started' "
            "ORDER BY ts_unix_ms DESC LIMIT 1 OFFSET ?",
            (max_sessions - 1,),
            limit=1,
        )
        if not rows:
            return 0

        cutoff_ts = int(rows[0]["ts_unix_ms"])
        try:
            r1 = await self._db.execute(
                "DELETE FROM events WHERE ts_unix_ms < ?", (cutoff_ts,)
            )
            r2 = await self._db.execute(
                "DELETE FROM request_snapshots WHERE ts_unix_ms < ?", (cutoff_ts,)
            )
            r3 = await self._db.execute(
                "DELETE FROM evaluations WHERE ts_unix_ms < ?", (cutoff_ts,)
            )
            await self._db.execute("PRAGMA incremental_vacuum")
            await self._db.commit()
            return max(r1.rowcount or 0, 0) + max(r2.rowcount or 0, 0) + max(r3.rowcount or 0, 0)
        except Exception as e:
            logger.error("Session retention failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
