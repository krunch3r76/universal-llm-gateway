"""SQLite event store - schema, insert, query, retention.

WAL mode with PRAGMA synchronous=NORMAL for high-throughput writes.
Generated virtual columns promote correlation fields from JSON payload
for indexed O(log N) lookups without schema churn.

Uses stdlib sqlite3 directly. Reads run on dedicated connections in worker
threads so a slow analytical query cannot block ingest or health probes.

Invariant: SQLite is the sole authoritative store for all queries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .retention import HEARTBEAT_SIGNALS
from .schema import _SCHEMA_SQL, migrate_correlation_taxonomy_columns

logger = logging.getLogger(__name__)

_REALTIME_BUFFER_SIZE = int(os.environ.get("REALTIME_BUFFER_SIZE", "10000"))
_SQLITE_CACHE_KIB = int(os.environ.get("EVENTS_SQLITE_CACHE_KIB", "1048576"))
_SQLITE_MMAP_BYTES = int(os.environ.get("EVENTS_SQLITE_MMAP_BYTES", str(8 * 1024**3)))

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
    """Convert ISO 8601 timestamp to Unix epoch milliseconds.

    Falls back to current wall-clock time when parsing fails, which preserves
    ingest continuity for malformed publisher timestamps.
    """
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, AttributeError):
        return int(time.time() * 1000)


class EventStore:
    """SQLite-backed event store with batched writes, retention, and realtime ring buffer."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: sqlite3.Connection | None = None
        self._reader_local = threading.local()
        self._realtime_buffer: deque[dict[str, Any]] = deque(
            maxlen=_REALTIME_BUFFER_SIZE
        )

    async def open(self) -> None:
        """Open SQLite, apply performance pragmas, and ensure schema exists."""
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._configure_connection(self._db)
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(_SCHEMA_SQL)
            migrate_correlation_taxonomy_columns(self._db)
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        logger.info("EventStore opened: %s", self._db_path)

    async def close(self) -> None:
        """Close the SQLite connection if it is open."""
        if self._db:
            self._db.close()
            self._db = None
        reader = getattr(self._reader_local, "connection", None)
        if reader is not None:
            reader.close()
            self._reader_local.connection = None

    @staticmethod
    def _configure_connection(db: sqlite3.Connection) -> None:
        """Apply RAM-oriented read settings without changing write durability."""
        db.execute(f"PRAGMA cache_size={-_SQLITE_CACHE_KIB}")
        db.execute(f"PRAGMA mmap_size={_SQLITE_MMAP_BYTES}")
        db.execute("PRAGMA temp_store=MEMORY")

    def _reader_connection(self) -> sqlite3.Connection:
        """Return one read-only connection per worker thread."""
        connection = getattr(self._reader_local, "connection", None)
        if connection is not None:
            return connection
        if self._db_path == ":memory:":
            connection = self._db
        else:
            uri = f"file:{Path(self._db_path).resolve()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self._configure_connection(connection)
            connection.execute("PRAGMA busy_timeout=5000")
        self._reader_local.connection = connection
        return connection

    def push_realtime(self, event: dict[str, Any]) -> None:
        """Push an event into the in-memory realtime ring buffer (no SQLite)."""
        self._realtime_buffer.append(event)

    def get_realtime_snapshot(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the last N events from the realtime ring buffer."""
        if limit <= 0:
            return []
        return list(self._realtime_buffer)[-limit:]

    async def insert_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert a batch of events. Returns the events with seq assigned.

        Skips events whose JSON payload exceeds 64KB. On DB error (e.g. disk
        full), logs and drops the batch to keep the service alive.
        """
        if not events:
            return []
        if not self._db:
            logger.error(
                "insert_events called before EventStore.open(); dropping batch"
            )
            return []

        rows: list[tuple[Any, ...]] = []
        accepted: list[dict[str, Any]] = []
        for ev in events:
            payload = ev.get("payload")
            payload_str = json.dumps(payload) if payload is not None else None
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
            event_id = ev.get("id")
            if event_id is not None:
                try:
                    event_id = int(event_id)
                except (TypeError, ValueError):
                    logger.warning(
                        "Dropping non-integer event id for signal=%s", ev.get("signal")
                    )
                    event_id = None
            rows.append(
                (
                    event_id,
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
            self._db.executemany(_INSERT_EVENT, rows)
            self._db.commit()
        except sqlite3.Error as e:
            logger.error(
                "DB write failed, dropping %d events (signals: %s): %s",
                len(rows),
                [ev.get("signal") for ev in accepted[:5]],
                e,
            )
            return []
        except Exception as e:
            logger.exception("Unexpected event insert failure: %s", e)
            return []

        return accepted

    async def insert_snapshot(self, snap: dict[str, Any]) -> None:
        """Insert a request snapshot record."""
        if not self._db:
            logger.error(
                "insert_snapshot called before EventStore.open(); request_id=%s phase=%s",
                snap.get("request_id"),
                snap.get("phase"),
            )
            return
        payload = snap.get("payload")
        payload_str = json.dumps(payload) if payload is not None else None
        try:
            self._db.execute(
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
            self._db.commit()
        except sqlite3.Error as e:
            logger.error(
                "Snapshot insert failed request_id=%s phase=%s: %s",
                snap.get("request_id"),
                snap.get("phase"),
                e,
            )
        except Exception as e:
            logger.exception(
                "Unexpected snapshot insert failure request_id=%s phase=%s: %s",
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
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute a read query and return rows as dicts.

        ``raise_on_error`` controls error visibility. Named operations keep the
        default (lenient: log + empty rows) so a hard-coded query bug does not
        crash an agent's investigation. User-facing raw SQL (the ``_raw_sql``
        handler) opts in to ``raise_on_error=True`` so typos and missing-column
        errors surface as structured 4xx responses instead of silent empty
        results.
        """
        if not self._db:
            logger.error("query called before EventStore.open(); sql=%s", sql[:120])
            if raise_on_error:
                raise sqlite3.OperationalError("EventStore not open")
            return []

        def read() -> list[dict[str, Any]]:
            cursor = self._reader_connection().execute(sql, params)
            return [dict(row) for row in cursor.fetchmany(limit)]

        try:
            return await asyncio.to_thread(read)
        except sqlite3.Error as e:
            logger.error("Query failed: %s params=%s - %s", sql[:120], params, e)
            if raise_on_error:
                raise
            return []
        except Exception as e:
            logger.exception("Unexpected query failure: %s", e)
            if raise_on_error:
                raise
            return []

    async def run_retention(self, max_age_ms: int) -> int:
        """Delete rows older than max_age_ms from all retained tables.

        Returns total count deleted across events, request_snapshots, and
        evaluations.
        """
        if not self._db:
            logger.error("run_retention called before EventStore.open()")
            return 0
        cutoff = int(time.time() * 1000) - max_age_ms
        try:
            r1 = self._db.execute("DELETE FROM events WHERE ts_unix_ms < ?", (cutoff,))
            r2 = self._db.execute(
                "DELETE FROM request_snapshots WHERE ts_unix_ms < ?", (cutoff,)
            )
            r3 = self._db.execute(
                "DELETE FROM evaluations WHERE ts_unix_ms < ?", (cutoff,)
            )
            self._db.execute("PRAGMA incremental_vacuum")
            self._db.commit()
            return (r1.rowcount or 0) + (r2.rowcount or 0) + (r3.rowcount or 0)
        except sqlite3.Error as e:
            logger.error("Retention failed cutoff=%s: %s", cutoff, e)
            return 0
        except Exception as e:
            logger.exception("Unexpected retention failure cutoff=%s: %s", cutoff, e)
            return 0

    async def prune_debug_events(self) -> int:
        """Delete debug events older than the current session boundary.

        Debug events (role='debug') are temporary diagnostic instrumentation.
        They survive within the current Stargate session only and are pruned at
        each retention cycle.
        """
        if not self._db:
            logger.error("prune_debug_events called before EventStore.open()")
            return 0

        rows = await self.query(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = 'system.started'",
            (),
            limit=1,
        )
        if not rows or rows[0].get("ts") is None:
            return 0

        cutoff_ts = int(rows[0]["ts"])
        try:
            result = self._db.execute(
                "DELETE FROM events WHERE role = 'debug' AND ts_unix_ms < ?",
                (cutoff_ts,),
            )
            self._db.commit()
            return result.rowcount or 0
        except sqlite3.Error as e:
            logger.error("Debug event prune failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected debug prune failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0

    async def prune_heartbeat_signals(self) -> int:
        """Delete heartbeat signals older than the current session boundary.

        Heartbeat signals are high-frequency telemetry that survive only
        within the current Stargate session and are pruned at each retention
        cycle.
        """
        if not self._db:
            logger.error("prune_heartbeat_signals called before EventStore.open()")
            return 0
        if not HEARTBEAT_SIGNALS:
            return 0

        rows = await self.query(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = 'system.started'",
            (),
            limit=1,
        )
        if not rows or rows[0].get("ts") is None:
            return 0

        cutoff_ts = int(rows[0]["ts"])
        placeholders = ", ".join("?" for _ in HEARTBEAT_SIGNALS)
        try:
            result = self._db.execute(
                f"DELETE FROM events WHERE signal IN ({placeholders}) AND ts_unix_ms < ?",
                (*sorted(HEARTBEAT_SIGNALS), cutoff_ts),
            )
            self._db.commit()
            return result.rowcount or 0
        except sqlite3.Error as e:
            logger.error("Heartbeat signal prune failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected heartbeat prune failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0

    async def run_session_retention(self, max_sessions: int) -> int:
        """Delete rows older than the Nth most recent system.started boundary.

        For max_sessions=2 this keeps rows from the two most recent Stargate
        sessions. Uses OFFSET max_sessions - 1 to identify the oldest boundary
        that should remain, then deletes older rows across all retained tables.
        """
        if max_sessions < 1:
            return 0
        if not self._db:
            logger.error("run_session_retention called before EventStore.open()")
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
            # Role-aware: coordination events are the forensic audit trail,
            # governed by the 7-day age cap (run_retention), NOT the session cap.
            # Skipping them here is what makes role="coordination" actually retain
            # past the 2-session boundary; without this filter the DELETE is
            # role-blind and any role promotion upstream is a no-op.
            r1 = self._db.execute(
                "DELETE FROM events WHERE ts_unix_ms < ? AND role != 'coordination'",
                (cutoff_ts,),
            )
            r2 = self._db.execute(
                "DELETE FROM request_snapshots WHERE ts_unix_ms < ?", (cutoff_ts,)
            )
            r3 = self._db.execute(
                "DELETE FROM evaluations WHERE ts_unix_ms < ?", (cutoff_ts,)
            )
            self._db.execute("PRAGMA incremental_vacuum")
            self._db.commit()
            return (r1.rowcount or 0) + (r2.rowcount or 0) + (r3.rowcount or 0)
        except sqlite3.Error as e:
            logger.error("Session retention failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected session retention failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0
