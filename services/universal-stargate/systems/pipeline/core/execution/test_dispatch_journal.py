from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from systems.pipeline.core.execution.async_tracker import (
    PipelineExecutionRecord,
    PipelineExecutionResult,
    PipelineExecutionTracker,
)
from systems.pipeline.core.execution.async_tracker_delivery.outcome import (
    DeliveryOutcome,
)
from systems.pipeline.core.execution.dispatch_journal import (
    _journal_path,
    fetch_record,
    fetch_terminal,
    initialize_schema,
    journal_terminal,
    journal_transition,
    prune_expired,
)
from systems.pipeline.core.execution.dispatch_journal_transitions import (
    migrate_schema_sync,
    write_transition_sync,
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _make_terminal_record(
    execution_id: str,
    *,
    completed_at: str,
    status: str = "completed",
) -> PipelineExecutionRecord:
    return PipelineExecutionRecord(
        execution_id=execution_id,
        pipeline="frontier-dispatch",
        status=status,
        started_at="2026-04-19T00:00:00Z",
        started_at_monotonic=0.0,
        completed_at=completed_at,
        completed_at_monotonic=1.0,
        result=PipelineExecutionResult(
            content="ok",
            model="openai/gpt-5.4",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            duration_s=1.0,
            reasoning=None,
        ),
    )


@pytest.mark.asyncio
async def test_journal_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    completed_at = _iso(datetime.now(UTC))
    record = _make_terminal_record("exec-roundtrip", completed_at=completed_at)
    await journal_terminal(record)

    fetched = await fetch_terminal("exec-roundtrip")
    assert fetched is not None
    assert fetched["execution_id"] == "exec-roundtrip"
    assert fetched["status"] == "completed"


@pytest.mark.asyncio
async def test_prune_keeps_fresh_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    old_ts = _iso(datetime.now(UTC) - timedelta(hours=26))
    new_ts = _iso(datetime.now(UTC) - timedelta(minutes=5))

    await journal_terminal(_make_terminal_record("exec-old", completed_at=old_ts))
    await journal_terminal(_make_terminal_record("exec-new", completed_at=new_ts))

    result = await prune_expired(retention_seconds=24 * 3600)
    assert result["records_deleted"] == 1

    assert await fetch_terminal("exec-old") is None
    assert await fetch_terminal("exec-new") is not None


@pytest.mark.asyncio
async def test_concurrent_writes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    completed_at = _iso(datetime.now(UTC))
    rec_a = _make_terminal_record("exec-a", completed_at=completed_at)
    rec_b = _make_terminal_record("exec-b", completed_at=completed_at, status="failed")

    await asyncio.gather(journal_terminal(rec_a), journal_terminal(rec_b))

    assert await fetch_terminal("exec-a") is not None
    assert await fetch_terminal("exec-b") is not None


@pytest.mark.asyncio
async def test_started_transition_survives_restart_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()
    record = PipelineExecutionRecord(
        execution_id="exec-started",
        pipeline="frontier-dispatch",
        status="running",
        started_at="2026-04-19T00:00:00Z",
        started_at_monotonic=0.0,
    )
    await journal_transition(record)
    fetched = await fetch_record("exec-started")
    assert fetched is not None
    assert fetched["status"] == "started"
    assert fetched["source"] == "pipeline_dispatch_journal"


@pytest.mark.asyncio
async def test_fetch_missing_record_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    assert await fetch_terminal("does-not-exist") is None


async def _drain_tracker(tracker: PipelineExecutionTracker) -> None:
    for _ in range(6):
        pending = list(tracker._pending_tasks)
        if not pending:
            await asyncio.sleep(0)
            pending = list(tracker._pending_tasks)
            if not pending:
                return
        await asyncio.gather(*pending)


@pytest.mark.asyncio
async def test_to_thread_started_then_one_fold_two_transitions(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """AC7: to_thread leaves started in flight; delivery folds once and logs two rows.

    Breaks when delivery resolves before the started write (empty in-flight read)
    or when the terminal write replaces the fold without appending a second
    transition (one log line, or two fold rows).
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    async def _delivered(_record: PipelineExecutionRecord) -> DeliveryOutcome:
        return DeliveryOutcome(status="delivered", thread="42")

    tracker = PipelineExecutionTracker(delivery_sender=_delivered)
    tracker.set_journal_writer(journal_terminal)
    tracker.set_transition_writer(journal_transition)
    tracker.register_execution(
        execution_id="exec-to-thread",
        pipeline="frontier-dispatch",
        started_at="2026-04-19T00:00:00Z",
        op="to_thread",
        target_thread="42",
        output_contract="thread",
    )
    await _drain_tracker(tracker)

    inflight = await fetch_record("exec-to-thread")
    assert inflight is not None
    assert inflight["status"] == "started"

    tracker.complete_execution(
        "exec-to-thread",
        content="posted",
        model="cursor/grok-4.7",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        duration_s=0.1,
    )
    await _drain_tracker(tracker)

    with sqlite3.connect(_journal_path()) as connection:
        fold_rows = connection.execute(
            "SELECT status FROM dispatch_records WHERE execution_id = ?",
            ("exec-to-thread",),
        ).fetchall()
        transitions = connection.execute(
            """
            SELECT status FROM dispatch_record_transitions
            WHERE execution_id = ?
            ORDER BY rowid
            """,
            ("exec-to-thread",),
        ).fetchall()
    assert [row[0] for row in fold_rows] == ["completed"]
    assert [row[0] for row in transitions] == ["started", "completed"]


@pytest.mark.asyncio
async def test_started_write_does_not_clobber_terminal_fold(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """D-race: late started transition must not replace a terminal fold row."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db_path = _journal_path()
    await initialize_schema()
    completed_at = _iso(datetime.now(UTC))
    terminal = _make_terminal_record("exec-race", completed_at=completed_at)
    await journal_terminal(terminal)

    write_transition_sync(
        db_path,
        execution_id="exec-race",
        pipeline=terminal.pipeline,
        status="started",
        caller_agent=None,
        started_at=terminal.started_at,
        completed_at=None,
        record_json={"execution_id": "exec-race", "status": "started"},
    )

    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT status FROM dispatch_records WHERE execution_id = ?",
            ("exec-race",),
        ).fetchone()
        transition_count = connection.execute(
            "SELECT COUNT(*) FROM dispatch_record_transitions WHERE execution_id = ?",
            ("exec-race",),
        ).fetchone()[0]
    assert row is not None
    assert row[0] == "completed"
    assert transition_count >= 2


@pytest.mark.asyncio
async def test_ac6_migrated_terminal_row_carries_basis_on_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Pre-change terminal JSON gains as_of/epoch/source on fetch after migration."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    db_path = _journal_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "execution_id": "exec-legacy",
        "status": "completed",
        "started_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-02T00:00:00Z",
    }
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE dispatch_records (
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
        )
        import json

        connection.execute(
            """
            INSERT INTO dispatch_records(
                execution_id, pipeline, status, caller_agent,
                started_at, completed_at, completed_at_epoch, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "exec-legacy",
                "frontier-dispatch",
                "completed",
                None,
                "2026-01-01T00:00:00Z",
                "2026-01-02T00:00:00Z",
                1_735_776_000.0,
                json.dumps(legacy_payload),
            ),
        )
        connection.commit()

    with sqlite3.connect(db_path) as connection:
        migrate_schema_sync(connection)

    fetched = await fetch_record("exec-legacy")
    assert fetched is not None
    assert fetched["source"] == "pipeline_dispatch_journal"
    assert fetched["as_of"] == "2026-01-02T00:00:00Z"
    assert isinstance(fetched.get("epoch"), dict)
    assert fetched["scope"] == "execution:exec-legacy"
