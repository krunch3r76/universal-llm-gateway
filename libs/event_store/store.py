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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import EventStoreBusyError, EventStoreReadDeadlineError, is_sqlite_busy
from .retention import HEARTBEAT_SIGNALS
from .schema import _SCHEMA_SQL, migrate_correlation_taxonomy_columns

logger = logging.getLogger(__name__)

_SESSION_BOUNDARY_SIGNAL = "event.service.started"
_write_fail_hook: Callable[[int, list[str], str], None] | None = None


def register_write_fail_hook(
    hook: Callable[[int, list[str], str], None] | None,
) -> None:
    """Register a callback invoked when insert_events drops rows on sqlite error."""
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
_READ_QUEUE_DEADLINE_S = float(os.environ.get("EVENTS_QUERY_QUEUE_DEADLINE_S", "5"))
_RETENTION_BATCH_SIZE = int(os.environ.get("EVENTS_RETENTION_BATCH_SIZE", "5000"))
_RETENTION_BATCH_SLEEP_S = float(
    os.environ.get("EVENTS_RETENTION_BATCH_SLEEP_S", "0.05")
)
_RETENTION_TABLES = frozenset({"events", "request_snapshots", "evaluations"})
_RETENTION_PK = {"events": "seq", "request_snapshots": "seq", "evaluations": "id"}


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


class _ReadCall:
    """Per-query interrupt state. The connection is the thread's, not the call's."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.phase = "queued"
        self.conn: sqlite3.Connection | None = None


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
        self._read_queue_deadline_s = _READ_QUEUE_DEADLINE_S
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

    def _prepare_insert_row(
        self, ev: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
        """Build one INSERT row or skip oversized / invalid-id events."""
        payload = ev.get("payload")
        payload_str = json.dumps(payload) if payload is not None else None
        if payload_str and len(payload_str.encode()) > _MAX_PAYLOAD_BYTES:
            logger.warning(
                "Dropping oversized event: signal=%s size=%d",
                ev.get("signal"),
                len(payload_str.encode()),
            )
            return None
        ts_iso = ev.get("timestamp") or ""
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
        row = (
            event_id,
            ev.get("signal") or "unknown",
            ev.get("role") or "observation",
            ev.get("scope") or "global",
            ts_ms,
            ts_iso,
            ev.get("source") or "unknown",
            payload_str,
        )
        return row, ev

    def _insert_prepared_rows(
        self, prepared: list[tuple[tuple[Any, ...], dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        """Insert rows one at a time after a batch failure; drop only failing rows."""
        assert self._db is not None
        accepted: list[dict[str, Any]] = []
        dropped = 0
        dropped_signals: list[str] = []
        last_err = ""
        for row, ev in prepared:
            try:
                self._db.execute(_INSERT_EVENT, row)
                self._db.commit()
                accepted.append(ev)
            except sqlite3.Error as e:
                self._db.rollback()
                dropped += 1
                dropped_signals.append(str(ev.get("signal", "unknown")))
                last_err = str(e)
                logger.warning(
                    "Dropping event on row insert: signal=%s err=%s",
                    ev.get("signal"),
                    e,
                )
            except Exception as e:
                self._db.rollback()
                dropped += 1
                dropped_signals.append(str(ev.get("signal", "unknown")))
                last_err = str(e)
                logger.exception("Unexpected row insert failure: %s", e)
        if dropped:
            logger.error(
                "DB write dropped %d event(s) after batch retry (signals: %s): %s",
                dropped,
                dropped_signals[:5],
                last_err,
            )
            if _write_fail_hook is not None:
                try:
                    _write_fail_hook(dropped, dropped_signals[:5], last_err)
                except Exception:
                    logger.exception("write_fail_hook raised")
        return accepted

    async def insert_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert a batch of events. Returns successfully persisted events.

        Skips events whose JSON payload exceeds 64KB. On batch DB error, rolls
        back and retries row-by-row so good rows commit and only bad rows drop.
        """
        if not events:
            return []
        if not self._db:
            logger.error(
                "insert_events called before EventStore.open(); dropping batch"
            )
            return []

        prepared: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        for ev in events:
            item = self._prepare_insert_row(ev)
            if item is not None:
                prepared.append(item)

        if not prepared:
            return []

        rows = [item[0] for item in prepared]
        try:
            self._db.executemany(_INSERT_EVENT, rows)
            self._db.commit()
        except sqlite3.Error as e:
            self._db.rollback()
            logger.warning(
                "Batch insert failed (%d rows), retrying one row at a time: %s",
                len(rows),
                e,
            )
            return self._insert_prepared_rows(prepared)
        except Exception as e:
            self._db.rollback()
            logger.exception("Unexpected batch insert failure: %s", e)
            return self._insert_prepared_rows(prepared)

        return [item[1] for item in prepared]

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

        Reads run on a private pool. Queue wait and execution have separate
        deadlines. The execution deadline starts when the worker marks the
        call running. On that deadline the interrupt targets only this call's
        connection, and only while its state is ``running`` under the call
        lock, so a late handler cannot abort the next read on the thread.
        ``EventStoreReadDeadlineError`` is raised even when ``raise_on_error``
        is false.
        """
        if not self._db:
            logger.error("query called before EventStore.open(); sql=%s", sql[:120])
            if raise_on_error:
                raise sqlite3.OperationalError("EventStore not open")
            return []

        state = _ReadCall()
        started = threading.Event()

        def read() -> list[dict[str, Any]]:
            connection = self._reader_connection()
            with state.lock:
                if state.phase == "abandoned":
                    return []
                state.phase = "running"
                state.conn = connection
                started.set()
            try:
                cursor = connection.execute(sql, params)
                return [dict(row) for row in cursor.fetchmany(limit)]
            finally:
                with state.lock:
                    state.conn = None
                    if state.phase == "running":
                        state.phase = "done"

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._read_pool(), read)
        queue_deadline = loop.time() + self._read_queue_deadline_s
        try:
            while not started.is_set():
                if future.done():
                    break
                if loop.time() >= queue_deadline:
                    with state.lock:
                        if state.phase == "queued":
                            state.phase = "abandoned"
                    self._abandon_read_future(future)
                    raise EventStoreReadDeadlineError(
                        f"event-store read queued past {self._read_queue_deadline_s}s"
                    )
                await asyncio.sleep(0.005)
            if future.done():
                return future.result()
            return await asyncio.wait_for(future, self._read_deadline_s)
        except TimeoutError:
            with state.lock:
                phase = state.phase
                conn = state.conn if phase == "running" else None
                if phase in ("queued", "running"):
                    state.phase = "abandoned"
            if (
                phase == "done"
                and future.done()
                and not future.cancelled()
                and future.exception() is None
            ):
                return future.result()
            if conn is not None:
                try:
                    conn.interrupt()
                except sqlite3.Error:
                    logger.exception("event-store reader interrupt failed")
            self._abandon_read_future(future)
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

    @staticmethod
    def _abandon_read_future(future: asyncio.Future[list[dict[str, Any]]]) -> None:
        """Retrieve a later worker exception so the timeout path does not warn."""

        def _consume(done: asyncio.Future[list[dict[str, Any]]]) -> None:
            if done.cancelled():
                return
            abandoned = done.exception()
            if abandoned is not None:
                logger.debug("abandoned event-store read: %s", abandoned)

        future.add_done_callback(_consume)

    async def _query_boundary(
        self, sql: str, params: tuple[Any, ...]
    ) -> list[dict[str, Any]] | None:
        """Run a retention boundary lookup.

        Returns rows, or ``None`` when the read pool raises a deadline or
        busy error. Those errors are logged and must not escape into
        ``_retention_loop``.
        """
        try:
            return await self.query(sql, params, limit=1)
        except (EventStoreReadDeadlineError, EventStoreBusyError) as exc:
            logger.error("Retention boundary lookup failed: %s", exc)
            return None

    async def _delete_bounded(
        self,
        table: str,
        where_sql: str,
        params: tuple[Any, ...],
        *,
        dry_run: bool,
    ) -> int:
        """Delete matching rows by primary-key keyset, off the event loop.

        Each batch selects the next ``batch_size`` primary keys above the
        last deleted key, then deletes those ids. A ``seq > last`` bound
        keeps retained rows (session coordination) from being rescanned on
        every batch. ``dry_run`` counts and deletes nothing. Sleep after a
        full batch is at least the batch elapsed time, so write-lock duty
        stays near half. A mid-pass SQLite error returns the count already
        committed.
        """
        if table not in _RETENTION_TABLES:
            raise ValueError(f"retention table not allowed: {table}")
        pk = _RETENTION_PK[table]
        batch_size = max(1, int(self._retention_batch_size))
        sleep_s = max(0.0, float(self._retention_batch_sleep_s))
        count_sql = f"SELECT COUNT(*) AS n FROM {table} WHERE {where_sql}"
        select_sql = (
            f"SELECT {pk} FROM {table} WHERE {pk} > ? AND ({where_sql}) "
            f"ORDER BY {pk} LIMIT ?"
        )

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
            last_pk = 0
            while True:
                started_batch = time.monotonic()
                try:
                    picked = conn.execute(
                        select_sql, (last_pk, *params, batch_size)
                    ).fetchall()
                    if not picked:
                        conn.commit()
                        break
                    ids = [int(row[0]) for row in picked]
                    last_pk = ids[-1]
                    placeholders = ", ".join("?" for _ in ids)
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE {pk} IN ({placeholders})",
                        ids,
                    )
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
                elapsed_ms = int((time.monotonic() - started_batch) * 1000)
                total += deleted
                logger.info(
                    "Retention batch table=%s deleted=%d cumulative=%d elapsed_ms=%d",
                    table,
                    deleted,
                    total,
                    elapsed_ms,
                )
                if deleted < batch_size:
                    break
                time.sleep(max(sleep_s, elapsed_ms / 1000))
            return total

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._retention_pool(), work)

    async def checkpoint_wal_truncate(self) -> None:
        """Truncate the WAL once after a retention pass that wrote.

        Logs the ``(busy, log, checkpointed)`` row from
        ``PRAGMA wal_checkpoint(TRUNCATE)``. Incremental vacuum is not run:
        live databases are ``auto_vacuum=0``, so it would not shrink the file.
        A checkpoint error is logged and does not raise.
        """
        if self._db is None:
            return

        def work() -> None:
            conn = self._retention_connection()
            row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            logger.info(
                "Retention WAL checkpoint mode=TRUNCATE result=%s",
                tuple(row) if row is not None else None,
            )

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._retention_pool(), work)
        except sqlite3.Error as e:
            logger.error("Retention WAL checkpoint failed: %s", e)

    async def run_retention(self, max_age_ms: int, *, dry_run: bool = False) -> int:
        """Delete rows older than max_age_ms from all retained tables.

        Deletes run as keyset batches off the event loop, with a sleep
        between batches of at least the batch elapsed time. ``dry_run``
        reports the row count it would delete and writes nothing. The WAL
        truncate is the caller's, once per pass.
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

        rows = await self._query_boundary(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
            (_SESSION_BOUNDARY_SIGNAL,),
        )
        if rows is None or not rows or rows[0].get("ts") is None:
            return 0

        cutoff_ts = int(rows[0]["ts"])
        try:
            deleted = await self._delete_bounded(
                "events",
                "role = 'debug' AND ts_unix_ms < ?",
                (cutoff_ts,),
                dry_run=dry_run,
            )
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

        rows = await self._query_boundary(
            "SELECT MAX(ts_unix_ms) AS ts FROM events WHERE signal = ?",
            (_SESSION_BOUNDARY_SIGNAL,),
        )
        if rows is None or not rows or rows[0].get("ts") is None:
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

        rows = await self._query_boundary(
            "SELECT ts_unix_ms FROM events WHERE signal = ? "
            "ORDER BY ts_unix_ms DESC LIMIT 1 OFFSET ?",
            (_SESSION_BOUNDARY_SIGNAL, max_sessions - 1),
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
            return deleted
        except sqlite3.Error as e:
            logger.error("Session retention failed cutoff_ts=%s: %s", cutoff_ts, e)
            return 0
        except Exception as e:
            logger.exception(
                "Unexpected session retention failure cutoff_ts=%s: %s", cutoff_ts, e
            )
            return 0
