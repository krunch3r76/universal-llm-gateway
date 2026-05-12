"""Regression tests for timeout visibility in recent-failures."""

from __future__ import annotations

import asyncio
from typing import Any

from event_store.operations_impl import _recent_failures
from event_store.store import EventStore


def test_recent_failures_includes_timeout_signals() -> None:
    async def _run() -> dict[str, Any]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "mcp.tool.file.read.timeout",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 1000,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "test",
                        "payload": {"path": "slow.pdf"},
                    },
                    {
                        "signal": "fs.timeout.suspected",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 2000,
                        "timestamp": "2026-01-01T00:00:02Z",
                        "source": "test",
                        "payload": {"tool_name": "fs"},
                    },
                    {
                        "signal": "mcp.request.completed",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 3000,
                        "timestamp": "2026-01-01T00:00:03Z",
                        "source": "test",
                        "payload": {"tool_name": "fs"},
                    },
                ]
            )
            return await _recent_failures({"limit": 10, "since_ts": 0}, store)
        finally:
            await store.close()

    body = asyncio.run(_run())

    assert body["count"] == 2
    assert [row["signal"] for row in body["rows"]] == [
        "fs.timeout.suspected",
        "mcp.tool.file.read.timeout",
    ]
