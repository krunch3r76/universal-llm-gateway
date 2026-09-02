"""Tests for operation trace session boundary after event.service.started rename."""

from __future__ import annotations

import asyncio

import pytest

from event_store.operations_trace import _stack_last_started
from event_store.store import EventStore


@pytest.mark.asyncio
async def test_stack_last_started_uses_event_service_started() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                {
                    "signal": "event.service.started",
                    "role": "coordination",
                    "scope": "global",
                    "ts_unix_ms": 42_000,
                    "timestamp": "2026-06-01T12:00:00Z",
                    "source": "event_service",
                    "payload": {},
                }
            ]
        )
        result = await _stack_last_started({}, store)
        assert result["stack_start_ts_unix_ms"] == 42_000
        assert result["stack_start_timestamp"] == "2026-06-01T12:00:00Z"
    finally:
        await store.close()


def test_startup_signals_exclude_system_started() -> None:
    from event_store.operations_trace import _STARTUP_SIGNALS

    assert "system.started" not in _STARTUP_SIGNALS
    assert "event.service.started" in _STARTUP_SIGNALS
