"""Persistent journal for terminal async-dispatch tracker records.

The in-memory tracker is the hot path. This module provides a cold-path
sqlite journal for terminal records so ``GET /api/v1/pipelines/executions/{id}``
can survive Stargate restarts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from universal_logging import get_logger

from ..events.dispatch import (
    PipelineDispatchJournalPruned,
    PipelineDispatchJournalRead,
    PipelineDispatchJournalWritten,
)

if TYPE_CHECKING:
    from universal_event_bus import Event

    from .async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


class _EventBusProtocol(Protocol):
    async def publish_nowait(self, event: Event) -> Any: ...


_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS dispatch_records (
    execution_id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    caller_agent TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    completed_at_epoch REAL NOT NULL,
    record_json TEXT NOT NULL
);
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_dispatch_records_completed_at
    ON dispatch_records(completed_at_epoch);
"""


def _default_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()


def _journal_path() -> Path:
    return _default_data_dir() / "pipeline-dispatch.db"


def _completed_epoch(iso_ts: str) -> float:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()


def _open_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    return connection


def _emit(event_bus: _EventBusProtocol | None, event: Event) -> None:
    if event_bus is None:
        return
    try:
        asyncio.create_task(event_bus.publish_nowait(event))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to publish dispatch-journal event: %s", exc)


def _initialize_schema_sync(path: Path) -> None:
    with _open_connection(path) as connection:
        connection.execute(_TABLE_SQL)
        connection.execute(_INDEX_SQL)


def _write_terminal_sync(path: Path, record: PipelineExecutionRecord) -> int:
    if record.completed_at is None:
        raise ValueError("Terminal record must include completed_at")
    payload = record.to_dict()
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    with _open_connection(path) as connection:
        connection.execute(_TABLE_SQL)
        connection.execute(_INDEX_SQL)
        connection.execute(
            """
            INSERT OR REPLACE INTO dispatch_records(
                execution_id, pipeline, status, caller_agent,
                started_at, completed_at, completed_at_epoch, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.execution_id,
                record.pipeline,
                record.status,
                record.caller_agent,
                record.started_at,
                record.completed_at,
                _completed_epoch(record.completed_at),
                payload_json,
            ),
        )
        connection.commit()
    return len(payload_json.encode("utf-8"))


def _fetch_terminal_sync(
    path: Path,
    execution_id: str,
) -> tuple[dict[str, Any], float] | None:
    with _open_connection(path) as connection:
        connection.execute(_TABLE_SQL)
        connection.execute(_INDEX_SQL)
        row = connection.execute(
            """
            SELECT record_json, completed_at_epoch
            FROM dispatch_records
            WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    payload_json, completed_at_epoch = row
    return json.loads(payload_json), float(completed_at_epoch)


def _prune_sync(path: Path, retention_seconds: float) -> tuple[int, float | None]:
    now = time.time()
    cutoff = now - retention_seconds
    with _open_connection(path) as connection:
        connection.execute(_TABLE_SQL)
        connection.execute(_INDEX_SQL)
        oldest_epoch_row = connection.execute(
            """
            SELECT MIN(completed_at_epoch)
            FROM dispatch_records
            WHERE completed_at_epoch < ?
            """,
            (cutoff,),
        ).fetchone()
        deleted = connection.execute(
            "DELETE FROM dispatch_records WHERE completed_at_epoch < ?",
            (cutoff,),
        ).rowcount
        connection.commit()
    oldest_epoch = None
    if oldest_epoch_row and oldest_epoch_row[0] is not None:
        oldest_epoch = float(oldest_epoch_row[0])
    oldest_age_seconds = (now - oldest_epoch) if oldest_epoch is not None else None
    return max(0, deleted), oldest_age_seconds


async def initialize_schema() -> None:
    """Ensure the sqlite journal schema exists."""
    await asyncio.to_thread(_initialize_schema_sync, _journal_path())


async def journal_terminal(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None = None,
) -> None:
    """Persist a terminal tracker record to sqlite."""
    if record.status not in {"completed", "failed"}:
        return
    if record.completed_at is None:
        return
    payload_bytes = await asyncio.to_thread(
        _write_terminal_sync,
        _journal_path(),
        record,
    )
    _emit(
        event_bus,
        PipelineDispatchJournalWritten(
            execution_id=record.execution_id,
            status=record.status,
            bytes_written=payload_bytes,
        ),
    )


async def fetch_terminal(
    execution_id: str,
    *,
    event_bus: _EventBusProtocol | None = None,
) -> dict[str, Any] | None:
    """Fetch a terminal record by execution id from the sqlite journal."""
    result = await asyncio.to_thread(
        _fetch_terminal_sync,
        _journal_path(),
        execution_id,
    )
    if result is None:
        return None
    payload, completed_at_epoch = result
    age_seconds = max(0.0, time.time() - completed_at_epoch)
    _emit(
        event_bus,
        PipelineDispatchJournalRead(
            execution_id=execution_id,
            age_seconds=age_seconds,
        ),
    )
    return payload


async def prune_expired(
    retention_seconds: float,
    *,
    event_bus: _EventBusProtocol | None = None,
) -> dict[str, float | int | None]:
    """Delete records older than ``retention_seconds`` and emit prune telemetry."""
    deleted, oldest_age_seconds = await asyncio.to_thread(
        _prune_sync,
        _journal_path(),
        retention_seconds,
    )
    _emit(
        event_bus,
        PipelineDispatchJournalPruned(
            records_deleted=deleted,
            oldest_deleted_age_seconds=oldest_age_seconds,
        ),
    )
    return {
        "records_deleted": deleted,
        "oldest_deleted_age_seconds": oldest_age_seconds,
    }
