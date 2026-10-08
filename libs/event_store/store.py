"""SQLite event store - schema, insert, query, retention.

WAL mode with PRAGMA synchronous=NORMAL for high-throughput writes.
Generated virtual columns promote correlation fields from JSON payload
for indexed O(log N) lookups without schema churn.

Uses stdlib sqlite3 directly. Reads run on a small private thread pool with
a server-side deadline so a slow scan cannot pin every worker. Retention
deletes are batched off the event loop.

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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .errors import EventStoreBusyError, EventStoreReadDeadlineError, is_sqlite_busy
from .retention import HEARTBEAT_SIGNALS
from .schema import _SCHEMA_SQL, migrate_correlation_taxonomy_columns

logger = logging.getLogger(__name__)

_SESSION_BOUNDARY_SIGNAL = "event.service.started"
_write_fail_hook: Callable[[int, list[str], str], None] | None = None


def register_write_fail_hook(
    hook: Callable[[int, list[str], str], None] | None,
) -> None:
    """Register a callback invoked when insert_events drops a batch on sqlite error."""
    global _write_fail_hook
    _write_fail_hook = hook


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
_READ_POOL_SIZE = int(os.environ.get("EVENTS_READ_POOL_SIZE", "4"))
_READ_DEADLINE_S = float(os.environ.get("EVENTS_QUERY_DEADLINE_S", "5"))
_RETENTION_BATCH_SIZE = int(os.environ.get("EVENTS_RETENTION_BATCH_SIZE", "5000"))
_RETENTION_BATCH_SLEEP_S = float(
    os.environ.get("EVENTS_RETENTION_BATCH_SLEEP_S", "0.05")
)
_RETENTION_TABLES = frozenset({"events", "request_snapshots", "evaluations"})


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
    """SQLite-backed event store with batched writes, retention, and a realtime ring.

    Call ``open`` before reads or writes and ``close`` to release connections
    and the read and retention executors. Reads go through ``query`` on a
    private pool; retention methods delete in bounded batches off the loop.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: sqlite3.Connection | None = None
        self._reader_local = threading.local()
        self._read_executor: ThreadPoolExecutor | None = None
        self._retention_executor: ThreadPoolExecutor | None = None
        self._retention_conn: sqlite3.Connection | None = None
        self._read_deadline_s = _READ_DEADLINE_S
        self._read_pool_size = _READ_POOL_SIZE
        self._retention_batch_size = _RETENTION_BATCH_SIZE
        self._retention_batch_sleep_s = _RETENTION_BATCH_SLEEP_S
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
            if os.environ.get("EVENTS_SQLITE_NFS_PIN") == "1":
                self._db.execute("PRAGMA locking_mode=EXCLUSIVE")
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
        """Close SQLite connections and shut down the read and retention pools."""
        db = self._db
        if self._read_executor is not None:
            self._read_executor.shutdown(wait=False, cancel_futures=True)
            self._read_executor = None
        if self._retention_executor is not None:
            self._retention_executor.shutdown(wait=False, cancel_futures=True)
            self._retention_executor = None
        if self._retention_conn is not None:
            self._retention_conn.close()
            self._retention_conn = None
        if db is not None:
            db.close()
            self._db = None
        reader = getattr(self._reader_local, "connection", None)
        if reader is not None and reader is not db:
            reader.close()
            self._reader_local.connection = None

    def _read_pool(self) -> ThreadPoolExecutor:
        """Return the private read pool, creating it on first use."""
        if self._read_executor is None:
            self._read_executor = ThreadPoolExecutor(
                max_workers=max(1, self._read_pool_size),
                thread_name_prefix="event-store-read",
            )
        return self._read_executor

    def _retention_pool(self) -> ThreadPoolExecutor:
        """Return the single-thread retention pool, creating it on first use."""
        if self._retention_executor is None:
            self._retention_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="event-store-retention",
            )
        return self._retention_executor

    def _retention_connection(self) -> sqlite3.Connection:
        """Return the connection retention batches must use.

        File databases get a dedicated connection so ingest can keep using
        ``self._db`` on the event loop. ``:memory:`` has no shared cache
        across connections, so retention reuses the open connection.
        """
        if self._db is None:
            raise RuntimeError("EventStore is not open")
        if self._db_path == ":memory:":
            return self._db
        if self._retention_conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            self._retention_conn = conn
        return self._retention_conn

    @staticmethod
    def _configure_connection(db: sqlite3.Connection) -> None:
        """Apply RAM-oriented read settings without changing write durability."""
        if os.environ.get("EVENTS_SQLITE_NFS_PIN") == "1":
            db.execute("PRAGMA mmap_size=0")
        else:
            db.execute(f"PRAGMA cache_size={-_SQLITE_CACHE_KIB}")
            db.execute(f"PRAGMA mmap_size={_SQLITE_MMAP_BYTES}")
        db.execute("PRAGMA temp_store=MEMORY")

    def _reader_connection(self) -> sqlite3.Connection:
        """Return one read-only connection per worker thread."""
        if os.environ.get("EVENTS_SQLITE_NFS_PIN") == "1" and self._db is not None:
            return self._db
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
            if _write_fail_hook is not None:
                try:
                    _write_fail_hook(
                        len(rows),
                        [str(ev.get("signal", "unknown")) for ev in accepted[:5]],
                        str(e),
                    )
                except Exception:
                    logger.exception("write_fail_hook raised")
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

        ``raise_on_error`` controls SQL error visibility. Named operations keep
        the default (lenient: log + empty rows) so a hard-coded query bug does
        not crash an agent's investigation. User-facing raw SQL (the
        ``_raw_sql`` handler) opts in to ``raise_on_error=True`` so typos and
        missing-column errors surface as structured 4xx responses instead of
        silent empty results.

        Reads run on a private pool. When the wait exceeds
        ``_read_deadline_s``, the reader connection is interrupted and this
        raises ``EventStoreReadDeadlineError`` even when ``raise_on_error``
        is false, so a scan cannot occupy a worker until the client gives up.
        """
        if not self._db:
            logger.error("query called before EventStore.open(); sql=%s", sql[:120])
            if raise_on_error:
                raise sqlite3.OperationalError("EventStore not open")
            return []

        started = threading.Event()
        slot: list[sqlite3.Connection] = []

        def read() -> list[dict[str, Any]]:
            connection = self._reader_connection()
            slot.append(connection)
            started.set()
            cursor = connection.execute(sql, params)
            return [dict(row) for row in cursor.fetchmany(limit)]

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._read_pool(), read)
        try:
            return await asyncio.wait_for(future, self._read_deadline_s)
        except TimeoutError:
            # wait_for marks the wrapper cancelled while the thread is still
            # inside execute, so cancellation is not evidence the read stopped.
            if future.done() and not future.cancelled() and future.exception() is None:
                return future.result()
            if started.is_set() and slot:
                try:
                    slot[0].interrupt()
                except sqlite3.Error:
                    logger.exception("event-store reader interrupt failed")

            def _consume(done: asyncio.Future[list[dict[str, Any]]]) -> None:
                if done.cancelled():
                    return
                abandoned = done.exception()
                if abandoned is not None:
                    logger.debug("abandoned event-store read: %s", abandoned)

            future.add_done_callback(_consume)
            raise EventStoreReadDeadlineError(
                f"event-store read exceeded deadline of {self._read_deadline_s}s"
            ) from None
        except sqlite3.Error as e:
            logger.error("Query failed: %s params=%s - %s", sql[:120], params, e)
            if is_sqlite_busy(e):
                raise EventStoreBusyError(str(e)) from e
            if raise_on_error:
                raise
            return []
        except Exception as e:
            logger.exception("Unexpected query failure: %s", e)
            if raise_on_error:
                raise
            return []

    async def _delete_bounded(
        self,
        table: str,
        where_sql: str,
        params: tuple[Any, ...],
        *,
        dry_run: bool,
    ) -> int:
        """Delete matching rows in ``DELETE … LIMIT N`` transactions off the loop.

        ``dry_run`` counts rows and deletes nothing, logging the count it would
        delete. Each real batch commits, logs ``deleted`` and ``cumulative``,
        then sleeps so other connections can read. A mid-pass SQLite error
        returns the count already committed. ``table`` must be one of
        ``_RETENTION_TABLES``; ``where_sql`` is a caller-built predicate.
        """
        if table not in _RETENTION_TABLES:
            raise ValueError(f"retention table not allowed: {table}")
        batch_size = max(1, int(self._retention_batch_size))
        sleep_s = max(0.0, float(self._retention_batch_sleep_s))
        count_sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {where_sql}"
        delete_sql = f"DELETE FROM {table} WHERE {where_sql} LIMIT ?"

        def work() -> int:
            conn = self._retention_connection()
            if dry_run:
                row = conn.execute(count_sql, params).fetchone()
                would_delete = int(row[0] if row is not None else 0)
                logger.info(
                    "Retention dry-run table=%s would_delete=%d",
                    table,
                    would_delete,
                )
                return would_delete
            total = 0
            while True:
                try:
                    cursor = conn.execute(delete_sql, (*params, batch_size))
                    deleted = cursor.rowcount or 0
                    conn.commit()
                except sqlite3.Error:
                    conn.rollback()
                    logger.exception(
                        "Retention batch failed table=%s cumulative=%d",
                        table,
                        total,
                    )
                    return total
                total += deleted
                logger.info(
                    "Retention batch table=%s deleted=%d cumulative=%d",
                    table,
                    deleted,
                    total,
                )
                if deleted < batch_size:
                    break
                time.sleep(sleep_s)
            return total

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._retention_pool(), work)

    async def _checkpoint_after_retention(self, *, dry_run: bool, vacuum: bool) -> None:
        """Run optional incremental vacuum and a passive WAL checkpoint off-loop.

        Dry-run skips both: it did not write. Vacuum matches the previous
        age and session retention passes; heartbeat and debug prunes checkpoint
        only. A checkpoint error is logged and does not raise.
        """
        if dry_run or self._db is None:
            return

        def work() -> None:
            conn = self._retention_connection()
            if vacuum:
                conn.execute("PRAGMA incremental_vacuum")
                conn.commit()
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            logger.info("Retention WAL checkpoint mode=PASSIVE vacuum=%s", vacuum)

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._retention_pool(), work)
        except sqlite3.Error as e:
            logger.error("Retention WAL checkpoint failed: %s", e)

    async def run_retention(self, max_age_ms: int, *, dry_run: bool = False) -> int:
        """Delete rows older than max_age_ms from all retained tables.

        Deletes run as bounded ``DELETE … LIMIT`` batches off the event loop,
        with a sleep between batches and a passive WAL checkpoint after.
        ``dry_run`` reports the row count it would delete and writes nothing.
        Returns the total deleted, or the would-delete count when ``dry_run``
        is true. A batch error returns the count already committed.
        """
        if not self._db:
            logger.error("run_retention called before EventStore.open()")
            return 0
        cutoff = int(time.time() * 1000) - max_age_ms
        try:
            deleted = 0
            for table in ("events", "request_snapshots", "evaluations"):
                deleted += await self._delete_bounded(
                    table,
                    "ts_unix_ms < ?",
                    (cutoff,),
                    dry_run=dry_run,
                )
            await self._checkpoint_after_retention(dry_run=dry_run, vacuum=True)
            return deleted
        except sqlite3.Error as e:
            logger.error("Retention failed cutoff=%s: %s", cutoff, e)
            return 0
        except Exception as e:
            logger.exception("Unexpected retention failure cutoff=%s: %s", cutoff, e)
            return 0

    async def prune_debug_events(self, *, dry_run: bool = False) -> int:
        """Delete debug events older than the current session boundary.

        Debug events (role='debug') are temporary diagnostic instrumentation.
        They survive within the current Stargate session only and are pruned at
        each retention cycle. The session lookup uses keyword ``limit`` so a
        stray positional argument cannot kill the retention loop. ``dry_run``
        counts matching rows and deletes nothing.
        """
        if not self._db:
            logger.error("prune_debug_events called before EventStore.open()")
            return 0

        rows = await self.query(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
            (_SESSION_BOUNDARY_SIGNAL,),
            limit=1,
        )
        if not rows or rows[0].get("ts") is None:
            return 0

        cutoff_ts = int(rows[0]["ts"])
        try:
            deleted = await self._delete_bounded(
                "events",
                "role = 'debug' AND ts_unix_ms < ?",
                (cutoff_ts,),
                dry_run=dry_run,
            )
            await self._checkpoint_after_retention(dry_run=dry_run, vacuum=False)
            return deleted
        except sqlite3.Error as e:
            logger.error("Debug event prune failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected debug prune failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0

    async def prune_heartbeat_signals(self, *, dry_run: bool = False) -> int:
        """Delete heartbeat signals older than the current session boundary.

        Heartbeat signals are high-frequency telemetry that survive only
        within the current Stargate session and are pruned at each retention
        cycle. The session lookup uses keyword ``limit``. ``dry_run`` counts
        matching rows and deletes nothing.
        """
        if not self._db:
            logger.error("prune_heartbeat_signals called before EventStore.open()")
            return 0
        if not HEARTBEAT_SIGNALS:
            return 0

        rows = await self.query(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
            (_SESSION_BOUNDARY_SIGNAL,),
            limit=1,
        )
        if not rows or rows[0].get("ts") is None:
            return 0

        cutoff_ts = int(rows[0]["ts"])
        placeholders = ", ".join("?" for _ in HEARTBEAT_SIGNALS)
        try:
            deleted = await self._delete_bounded(
                "events",
                f"signal IN ({placeholders}) AND ts_unix_ms < ?",
                (*sorted(HEARTBEAT_SIGNALS), cutoff_ts),
                dry_run=dry_run,
            )
            await self._checkpoint_after_retention(dry_run=dry_run, vacuum=False)
            return deleted
        except sqlite3.Error as e:
            logger.error("Heartbeat signal prune failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected heartbeat prune failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0

    async def run_session_retention(
        self, max_sessions: int, *, dry_run: bool = False
    ) -> int:
        """Delete rows older than the Nth most recent event.service.started boundary.

        For max_sessions=2 this keeps rows from the two most recent Stargate
        sessions. Uses OFFSET max_sessions - 1 to identify the oldest boundary
        that should remain, then deletes older rows across all retained tables
        in bounded batches. Coordination events are skipped here; the age cap
        in ``run_retention`` is what keeps that role past the session window.
        ``dry_run`` counts rows and deletes nothing.
        """
        if max_sessions < 1:
            return 0
        if not self._db:
            logger.error("run_session_retention called before EventStore.open()")
            return 0

        rows = await self.query(
            "SELECT ts_unix_ms FROM events WHERE signal = ? "
            "ORDER BY ts_unix_ms DESC LIMIT 1 OFFSET ?",
            (_SESSION_BOUNDARY_SIGNAL, max_sessions - 1),
            limit=1,
        )
        if not rows:
            return 0

        cutoff_ts = int(rows[0]["ts_unix_ms"])
        try:
            deleted = await self._delete_bounded(
                "events",
                "ts_unix_ms < ? AND role != 'coordination'",
                (cutoff_ts,),
                dry_run=dry_run,
            )
            deleted += await self._delete_bounded(
                "request_snapshots",
                "ts_unix_ms < ?",
                (cutoff_ts,),
                dry_run=dry_run,
            )
            deleted += await self._delete_bounded(
                "evaluations",
                "ts_unix_ms < ?",
                (cutoff_ts,),
                dry_run=dry_run,
            )
            await self._checkpoint_after_retention(dry_run=dry_run, vacuum=True)
            return deleted
        except sqlite3.Error as e:
            logger.error("Session retention failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected session retention failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0
