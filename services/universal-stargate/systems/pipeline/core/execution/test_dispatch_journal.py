from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from systems.pipeline.core.execution.async_tracker import (
    PipelineExecutionRecord,
    PipelineExecutionResult,
)
from systems.pipeline.core.execution.dispatch_journal import (
    fetch_terminal,
    initialize_schema,
    journal_terminal,
    prune_expired,
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
async def test_fetch_missing_record_returns_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    await initialize_schema()

    assert await fetch_terminal("does-not-exist") is None
