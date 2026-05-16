"""Tests for pipeline-trace not-found vs aged-out diagnostics."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from event_store.operations_trace import _pipeline_trace
from event_store.store import EventStore


@pytest.mark.asyncio
async def test_pipeline_trace_not_found_includes_retention_floor() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "system.started",
                    "role": "coordination",
                    "scope": "global",
                    "timestamp": "2026-05-16T00:00:00Z",
                    "source": "test",
                    "payload": {},
                }
            ]
        )

        result = await _pipeline_trace({"execution_id": "missing"}, store)

        assert result["error"]["code"] == "pipeline_trace_not_found"
        assert result["retention"]["event_count"] == 1
        assert result["retention"]["floor_timestamp"] == "2026-05-16T00:00:00Z"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_pipeline_trace_aged_out_when_dispatch_journal_has_record(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    journal_path = tmp_path / "pipeline-dispatch.db"
    with sqlite3.connect(journal_path) as conn:
        conn.execute(
            """
            CREATE TABLE dispatch_records (
                execution_id TEXT PRIMARY KEY,
                pipeline TEXT NOT NULL,
                status TEXT NOT NULL,
                caller_agent TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                completed_at_epoch REAL NOT NULL,
                record_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dispatch_records (
                execution_id, pipeline, status, caller_agent,
                started_at, completed_at, completed_at_epoch, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-exec",
                "frontier-dispatch",
                "completed",
                "cursor",
                "2026-05-12T21:37:00Z",
                "2026-05-12T21:38:00Z",
                1.0,
                json.dumps(
                    {
                        "result": {
                            "model": "frontier-dispatch",
                            "model_entity_id": "model:gemini-2.5-pro",
                        }
                    }
                ),
            ),
        )

    store = EventStore(":memory:")
    await store.open()
    try:
        result = await _pipeline_trace({"execution_id": "old-exec"}, store)

        assert result["error"]["code"] == "pipeline_trace_aged_out"
        assert result["cold_record"]["pipeline"] == "frontier-dispatch"
        assert result["cold_record"]["model"] == "frontier-dispatch"
        assert result["cold_record"]["model_entity_id"] == "model:gemini-2.5-pro"
    finally:
        await store.close()
