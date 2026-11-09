"""In-memory SQLite hot tier mirroring recent cold-store event rows."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

from .schema import (
    _SCHEMA_SQL,
    build_model_id_ts_index,
    migrate_correlation_taxonomy_columns,
)

logger = logging.getLogger(__name__)


class HotTierReadBudgetExceeded(sqlite3.OperationalError):
    """Hot-tier read stopped by the progress-handler time budget."""


_WARM_CHUNK = 5000
_HOT_READ_BUDGET_S = 0.25
_EVENT_COLS = (
    "seq",
    "event_id",
    "signal",
    "role",
    "scope",
    "ts_unix_ms",
    "timestamp",
    "source",
    "payload",
)
_INSERT_HOT = (
    "INSERT OR IGNORE INTO events (seq, event_id, signal, role, scope, ts_unix_ms, "
    "timestamp, source, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_WARM = (
    f"SELECT {', '.join(_EVENT_COLS)} FROM events INDEXED BY idx_ts_unix_ms "
    "WHERE ts_unix_ms >= ? AND (ts_unix_ms > ? OR (ts_unix_ms = ? AND seq > ?)) "
    "ORDER BY ts_unix_ms ASC, seq ASC LIMIT ?"
)


class HotTier:
    """Shared-cache in-memory SQLite tier with one writer and pooled readers."""

    def __init__(self, *, window_ms: int) -> None:
        self.window_ms = window_ms
        hot_id = uuid.uuid4().hex
        self._uri = f"file:events_hot_{hot_id}?mode=memory&cache=shared"
        self._lock = threading.Lock()
        self._writer: sqlite3.Connection | None = None
        self._reader_local = threading.local()
        self.floor_ts_ms = 0
        self._warm_started_floor = 0
        self.degraded = False
        self.last_evict_ms = 0
        self.last_seq = 0

    def open(self, cold: sqlite3.Connection) -> None:
        """Create schema on the hot writer and warm-copy recent rows from cold."""
        try:
            conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.executescript(_SCHEMA_SQL)
            migrate_correlation_taxonomy_columns(conn)
            conn.commit()
            build_model_id_ts_index(conn)
            self._writer = conn
            self._warm_from_cold(cold)
        except Exception as exc:
            logger.exception("Hot tier open failed: %s", exc)
            self.mark_degraded(str(exc))

    def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        reader = getattr(self._reader_local, "connection", None)
        if reader is not None and reader is not self._writer:
            reader.close()
            self._reader_local.connection = None

    def _warm_from_cold(self, cold: sqlite3.Connection) -> None:
        assert self._writer is not None
        t0 = time.perf_counter()
        now_ms = int(time.time() * 1000)
        floor = now_ms - self.window_ms
        self.floor_ts_ms = floor
        self._warm_started_floor = floor
        last_ts = floor
        last_seq = 0
        total = 0
        max_seq = 0
        while True:
            rows = cold.execute(
                _SELECT_WARM, (last_ts, last_ts, last_ts, last_seq, _WARM_CHUNK)
            ).fetchall()
            if not rows:
                break
            with self._lock:
                self._writer.executemany(_INSERT_HOT, [tuple(row) for row in rows])
                self._writer.commit()
            last_ts = int(rows[-1][5])
            last_seq = int(rows[-1][0])
            max_seq = max(max_seq, last_seq)
            total += len(rows)
            if len(rows) < _WARM_CHUNK:
                break
        max_cold_row = cold.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events"
        ).fetchone()
        cold_max_seq = int(max_cold_row[0] if max_cold_row is not None else 0)
        self.last_seq = max(max_seq, cold_max_seq)
        elapsed_s = time.perf_counter() - t0
        logger.info(
            "Hot tier warmed rows=%d floor_ts_ms=%d window_ms=%d elapsed_s=%.3f",
            total,
            self.floor_ts_ms,
            self.window_ms,
            elapsed_s,
        )

    def mark_degraded(self, reason: str) -> None:
        if not self.degraded:
            logger.error(
                "Hot tier degraded; routing to cold until next warm: %s", reason
            )
        self.degraded = True

    def row_count(self) -> int:
        if self._writer is None:
            return 0
        row = self._writer.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row[0] if row is not None else 0)

    def stats(self) -> dict[str, Any]:
        return {
            "rows": self.row_count(),
            "floor_ts_ms": self.floor_ts_ms,
            "degraded": self.degraded,
            "last_evict_ms": self.last_evict_ms,
            "window_ms": self.window_ms,
        }

    def _reader_connection(self) -> sqlite3.Connection:
        connection = getattr(self._reader_local, "connection", None)
        if connection is not None:
            return connection
        connection = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA temp_store=MEMORY")
        self._reader_local.connection = connection
        return connection

    def execute_query(
        self, sql: str, params: tuple[Any, ...], *, limit: int
    ) -> list[dict[str, Any]]:
        conn = self._reader_connection()
        deadline = time.monotonic() + _HOT_READ_BUDGET_S
        budget_armed = False

        def _progress_handler() -> int:
            nonlocal budget_armed
            if time.monotonic() >= deadline:
                budget_armed = True
                return 1
            return 0

        conn.set_progress_handler(_progress_handler, 1000)
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchmany(limit)]
        except sqlite3.OperationalError as exc:
            if budget_armed or "interrupted" in str(exc).lower():
                raise HotTierReadBudgetExceeded(str(exc)) from exc
            raise
        finally:
            conn.set_progress_handler(None, 0)

    def insert_rows(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows or self._writer is None:
            return
        with self._lock:
            self._writer.executemany(_INSERT_HOT, rows)
            self._writer.commit()
        for row in rows:
            seq = int(row[0])
            if seq > self.last_seq:
                self.last_seq = seq

    def evict(self, now_ms: int) -> int:
        if self._writer is None:
            return 0
        cutoff = now_ms - self.window_ms
        with self._lock:
            cursor = self._writer.execute(
                "DELETE FROM events WHERE ts_unix_ms < ?", (cutoff,)
            )
            deleted = int(cursor.rowcount or 0)
            self._writer.commit()
        new_floor = max(self.floor_ts_ms, cutoff)
        self.floor_ts_ms = new_floor
        self.last_evict_ms = now_ms
        if deleted:
            logger.info(
                "Hot tier evicted deleted=%d floor_ts_ms=%d", deleted, self.floor_ts_ms
            )
        return deleted
