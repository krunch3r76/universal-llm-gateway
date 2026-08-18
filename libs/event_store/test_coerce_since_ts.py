"""Seconds-vs-milliseconds since_ts coercion for signal-events and sibling ops."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from event_store.operation_parameters import _coerce_since_ts
from event_store.operations_impl import (
    _coordination_audit,
    _model_timeline,
    _recent_failures,
    _signal_events,
)
from event_store.store import EventStore

# Specimen CLOSEOUT auto-80dc3e0e2c8b: Unix seconds that used to pass every row.
_SPECIMEN_SINCE_TS_SECONDS = 1_787_059_116
_SPECIMEN_SINCE_TS_MS = _SPECIMEN_SINCE_TS_SECONDS * 1000
_ROW_131701_MS = int(datetime(2026, 8, 18, 13, 17, 1, tzinfo=UTC).timestamp() * 1000)
_SIGNAL = "mcp.transport.request.started"


@pytest.mark.offline
def test_coerce_since_ts_seconds_and_ms_are_identical() -> None:
    assert _coerce_since_ts(_SPECIMEN_SINCE_TS_SECONDS) == _SPECIMEN_SINCE_TS_MS
    assert _coerce_since_ts(_SPECIMEN_SINCE_TS_MS) == _SPECIMEN_SINCE_TS_MS
    assert _coerce_since_ts(str(_SPECIMEN_SINCE_TS_SECONDS)) == _SPECIMEN_SINCE_TS_MS


@pytest.mark.offline
def test_coerce_since_ts_zero_none_invalid() -> None:
    assert _coerce_since_ts(0) == 0
    assert _coerce_since_ts(None) is None
    assert _coerce_since_ts("not-a-ts") is None


async def _run_signal_events(since_ts: int) -> dict[str, Any]:
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": _SIGNAL,
                    "role": "observation",
                    "scope": "global",
                    "ts_unix_ms": _ROW_131701_MS,
                    "timestamp": "2026-08-18T13:17:01Z",
                    "source": "mcp-server",
                    "payload": {"when": "13:17:01"},
                },
                {
                    "signal": _SIGNAL,
                    "role": "observation",
                    "scope": "global",
                    "ts_unix_ms": _SPECIMEN_SINCE_TS_MS + 60_000,
                    "timestamp": "2026-08-18T13:19:36Z",
                    "source": "mcp-server",
                    "payload": {"when": "after-cutoff"},
                },
            ]
        )
        return await _signal_events(
            {"signal": _SIGNAL, "limit": 10, "since_ts": since_ts},
            store,
        )
    finally:
        await store.close()


@pytest.mark.offline
def test_signal_events_seconds_and_ms_filter_identically() -> None:
    seconds_body = asyncio.run(_run_signal_events(_SPECIMEN_SINCE_TS_SECONDS))
    ms_body = asyncio.run(_run_signal_events(_SPECIMEN_SINCE_TS_MS))
    seconds_when = [row["payload"]["when"] for row in seconds_body["rows"]]
    ms_when = [row["payload"]["when"] for row in ms_body["rows"]]
    assert seconds_when == ms_when
    assert "13:17:01" not in seconds_when
    assert seconds_when == ["after-cutoff"]
    assert seconds_body["count"] == 1
    assert ms_body["count"] == 1


@pytest.mark.offline
def test_sibling_ops_use_same_coercion() -> None:
    """Bug-class sweep: named ops that share _coerce_since_ts see milliseconds."""

    async def _run() -> dict[str, int]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "worker.failed",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": _ROW_131701_MS,
                        "timestamp": "2026-08-18T13:17:01Z",
                        "source": "mcp-server",
                        "payload": {"model_id": "cursor/grok-4.6"},
                    },
                    {
                        "signal": "worker.ok",
                        "role": "coordination",
                        "scope": "global",
                        "ts_unix_ms": _SPECIMEN_SINCE_TS_MS + 60_000,
                        "timestamp": "2026-08-18T13:19:36Z",
                        "source": "mcp-server",
                        "payload": {"model_id": "cursor/grok-4.6"},
                    },
                ]
            )
            failures = await _recent_failures(
                {"limit": 10, "since_ts": _SPECIMEN_SINCE_TS_SECONDS},
                store,
            )
            audit = await _coordination_audit(
                {"limit": 10, "since_ts": _SPECIMEN_SINCE_TS_SECONDS},
                store,
            )
            timeline = await _model_timeline(
                {
                    "model_id": "cursor/grok-4.6",
                    "since_ts": _SPECIMEN_SINCE_TS_SECONDS,
                },
                store,
            )
            return {
                "failures": failures["count"],
                "audit": audit["count"],
                "timeline": timeline["count"],
            }
        finally:
            await store.close()

    counts = asyncio.run(_run())
    assert counts["failures"] == 0
    assert counts["audit"] == 1
    assert counts["timeline"] == 1
