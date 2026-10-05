"""Persistent journal for terminal async-dispatch tracker records.

The in-memory tracker is the hot path. This module provides a cold-path
sqlite journal for terminal records so ``GET /api/v1/executions/{id}``
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
from .dispatch_journal_transitions import (
    fetch_record_sync,
    migrate_schema_sync,
    prune_started_sync,
    write_transition_sync,
)

if TYPE_CHECKING:
    from universal_event_bus import Event

    from .async_tracker import PipelineExecutionRecord

logger = get_logger(__name__)


class _EventBusProtocol(Protocol):
    async def publish_nowait(self, event: Event) -> Any: ...


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
        migrate_schema_sync(connection)
        connection.execute(_INDEX_SQL)


def _write_terminal_sync(
    path: Path,
    record: PipelineExecutionRecord,
    payload: dict[str, Any] | None = None,
) -> int:
    if record.completed_at is None:
        raise ValueError("Terminal record must include completed_at")
    body = payload if payload is not None else record.to_dict()
    payload_bytes = write_transition_sync(
        path,
        execution_id=record.execution_id,
        pipeline=record.pipeline,
        status=record.status,
        caller_agent=record.caller_agent,
        started_at=record.started_at,
        completed_at=record.completed_at,
        record_json=body,
    )
    return payload_bytes


def _fetch_terminal_sync(
    path: Path,
    execution_id: str,
) -> tuple[dict[str, Any], float] | None:
    with _open_connection(path) as connection:
        migrate_schema_sync(connection)
        row = connection.execute(
            """
            SELECT record_json, completed_at_epoch
            FROM dispatch_records
            WHERE execution_id = ? AND status IN ('completed', 'failed')
            """,
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    payload_json, completed_at_epoch = row
    if completed_at_epoch is None:
        return None
    return json.loads(payload_json), float(completed_at_epoch)


def _prune_sync(
    path: Path, retention_seconds: float
) -> tuple[int, float | None, int]:
    now = time.time()
    cutoff = now - retention_seconds
    started_deleted = prune_started_sync(path, retention_seconds)
    with _open_connection(path) as connection:
        migrate_schema_sync(connection)
        connection.execute(_INDEX_SQL)
        oldest_epoch_row = connection.execute(
            """
            SELECT MIN(completed_at_epoch)
            FROM dispatch_records
            WHERE completed_at_epoch IS NOT NULL AND completed_at_epoch < ?
            """,
            (cutoff,),
        ).fetchone()
        deleted = connection.execute(
            """
            DELETE FROM dispatch_records
            WHERE completed_at_epoch IS NOT NULL AND completed_at_epoch < ?
            """,
            (cutoff,),
        ).rowcount
        connection.commit()
    oldest_epoch = None
    if oldest_epoch_row and oldest_epoch_row[0] is not None:
        oldest_epoch = float(oldest_epoch_row[0])
    oldest_age_seconds = (now - oldest_epoch) if oldest_epoch is not None else None
    return max(0, deleted), oldest_age_seconds, started_deleted


async def initialize_schema() -> None:
    """Create the ``dispatch_records`` table and its index if missing, off the event
    loop.

    Runs the blocking sqlite DDL via ``asyncio.to_thread`` against
    ``$DATA_DIR/pipeline-dispatch.db`` (default ``~/.gateway``). Idempotent. Called once
    by ``initialize_dispatch_journal`` at Stargate startup before the tracker's journal
    writer is installed.
    """
    await asyncio.to_thread(_initialize_schema_sync, _journal_path())


async def journal_terminal(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None = None,
) -> None:
    """Upsert a completed or failed tracker record into the sqlite dispatch journal.

    Silently returns for non-terminal statuses or records without ``completed_at``.
    Otherwise writes ``record.to_dict()`` as JSON via ``INSERT OR REPLACE`` on a worker
    thread and, when ``event_bus`` is given, fire-and-forget publishes
    ``PipelineDispatchJournalWritten`` with the byte size. Installed (via ``partial``)
    as the async tracker's journal writer by ``initialize_dispatch_journal``.
    """
    if record.status not in {"completed", "failed"}:
        return
    if record.completed_at is None:
        return
    from universal_protocol.status_basis import (
        SOURCE_PIPELINE_DISPATCH_JOURNAL,
        status_basis,
    )

    base = record.to_dict()
    enriched = status_basis(
        "status",
        record.status,
        as_of=record.completed_at,
        source=SOURCE_PIPELINE_DISPATCH_JOURNAL,
        scope=f"execution:{record.execution_id}",
        epoch={"writer": "pipeline_dispatch_journal"},
        recovery={"consulted": ["pipeline_tracker", "pipeline_dispatch_journal"]},
        state=record.status,
        **{k: v for k, v in base.items() if k not in {"status", "state"}},
    )
    payload_bytes = await asyncio.to_thread(
        _write_terminal_sync,
        _journal_path(),
        record,
        enriched,
    )
    _emit(
        event_bus,
        PipelineDispatchJournalWritten(
            execution_id=record.execution_id,
            status=record.status,
            bytes_written=payload_bytes,
        ),
    )


async def journal_transition(
    record: PipelineExecutionRecord,
    *,
    status: str = "started",
    event_bus: _EventBusProtocol | None = None,
) -> None:
    """Persist a non-terminal ``started`` fold row. Callers may fire-and-forget; other statuses are ignored."""
    if status != "started":
        return
    payload = record.to_dict()
    from universal_protocol.status_basis import (
        SOURCE_PIPELINE_DISPATCH_JOURNAL,
        status_basis,
    )

    payload = status_basis(
        "status",
        "started",
        as_of=record.started_at,
        source=SOURCE_PIPELINE_DISPATCH_JOURNAL,
        scope=f"execution:{record.execution_id}",
        epoch={"writer": "pipeline_dispatch_journal"},
        recovery={"consulted": ["pipeline_tracker", "pipeline_dispatch_journal"]},
        **{k: v for k, v in payload.items() if k not in {"status", "state"}},
    )
    try:
        payload_bytes = await asyncio.to_thread(
            write_transition_sync,
            _journal_path(),
            execution_id=record.execution_id,
            pipeline=record.pipeline,
            status="started",
            caller_agent=record.caller_agent,
            started_at=record.started_at,
            completed_at=None,
            record_json=payload,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to journal started transition: %s", exc)
        return
    _emit(
        event_bus,
        PipelineDispatchJournalWritten(
            execution_id=record.execution_id,
            status="started",
            bytes_written=payload_bytes,
        ),
    )


def _enrich_legacy_journal_payload(
    payload: dict[str, Any],
    *,
    execution_id: str,
    row_status: str,
) -> dict[str, Any]:
    """Stamp AC4 basis fields on pre-status_basis journal rows after migration."""
    if payload.get("source") and payload.get("as_of") and payload.get("epoch"):
        return payload
    from universal_protocol.status_basis import (
        SOURCE_PIPELINE_DISPATCH_JOURNAL,
        status_basis,
    )

    as_of = payload.get("completed_at") or payload.get("started_at") or ""
    base = status_basis(
        "status",
        str(payload.get("status") or row_status),
        as_of=str(as_of),
        source=SOURCE_PIPELINE_DISPATCH_JOURNAL,
        scope=f"execution:{execution_id}",
        epoch={"writer": "pipeline_dispatch_journal", "migrated_read": True},
        recovery={"consulted": ["pipeline_tracker", "pipeline_dispatch_journal"]},
        state=str(payload.get("status") or row_status),
    )
    merged = {**payload, **base}
    merged.setdefault("status", row_status)
    return merged


async def fetch_record(
    execution_id: str,
    *,
    event_bus: _EventBusProtocol | None = None,
) -> dict[str, Any] | None:
    """Fetch a journal record of any status from sqlite, or None when the execution id is absent."""
    result = await asyncio.to_thread(
        fetch_record_sync,
        _journal_path(),
        execution_id,
    )
    if result is None:
        return None
    payload, row_status, updated_epoch = result
    payload = _enrich_legacy_journal_payload(
        payload, execution_id=execution_id, row_status=row_status
    )
    age_seconds = max(0.0, time.time() - updated_epoch)
    _emit(
        event_bus,
        PipelineDispatchJournalRead(
            execution_id=execution_id,
            age_seconds=age_seconds,
        ),
    )
    return payload


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
    payload = _enrich_legacy_journal_payload(
        payload,
        execution_id=execution_id,
        row_status=str(payload.get("status") or "completed"),
    )
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
    """Delete records older than ``retention_seconds`` and emit prune telemetry.

    Age is measured from ``completed_at_epoch``. Runs the sqlite DELETE on a worker
    thread, publishes ``PipelineDispatchJournalPruned`` when ``event_bus`` is given, and
    returns a dict with ``records_deleted`` and ``oldest_deleted_age_seconds`` (None if
    nothing was deleted). Invoked hourly by the Stargate dispatch-journal prune loop.
    """
    deleted, oldest_age_seconds, started_deleted = await asyncio.to_thread(
        _prune_sync,
        _journal_path(),
        retention_seconds,
    )
    _emit(
        event_bus,
        PipelineDispatchJournalPruned(
            records_deleted=deleted,
            oldest_deleted_age_seconds=oldest_age_seconds,
            started_records_deleted=started_deleted,
        ),
    )
    return {
        "records_deleted": deleted,
        "oldest_deleted_age_seconds": oldest_age_seconds,
        "started_records_deleted": started_deleted,
    }
