"""Regression tests for signal-events pattern matching."""

from __future__ import annotations

import asyncio
from typing import Any

from event_store.operation_parameters import _signal_match_sql
from event_store.operations_impl import _signal_events
from event_store.store import EventStore


def test_signal_match_sql_accepts_percent_and_star() -> None:
    assert _signal_match_sql("mcp.transport.%") == ("LIKE ?", "mcp.transport.%")
    assert _signal_match_sql("mcp.transport.*") == (
        "LIKE ? ESCAPE '\\'",
        "mcp.transport.%",
    )
    assert _signal_match_sql("mcp.*.request.%") == (
        "LIKE ? ESCAPE '\\'",
        "mcp.%.request.\\%",
    )
    assert _signal_match_sql("mcp.transport.request.started") == (
        "= ?",
        "mcp.transport.request.started",
    )


def test_signal_match_sql_escapes_underscore_on_glob_path() -> None:
    # C2: a literal ``_`` in a glob pattern must not act as a LIKE single-char
    # wildcard. The escaped bind value pairs with the ESCAPE '\\' clause.
    predicate, value = _signal_match_sql("team_dispatch.*")
    assert predicate == "LIKE ? ESCAPE '\\'"
    assert value == "team\\_dispatch.%"


def test_signal_events_percent_wildcard_matches_transport_family() -> None:
    async def _run() -> dict[str, Any]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "mcp.transport.request.started",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 1000,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "mcp-stdio-proxy",
                        "payload": {"transport": "stdio"},
                    },
                    {
                        "signal": "mcp.cortex.dispatch",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 2000,
                        "timestamp": "2026-01-01T00:00:02Z",
                        "source": "mcp-server",
                        "payload": {},
                    },
                ]
            )
            return await _signal_events(
                {"signal": "mcp.transport.%", "limit": 10, "since_ts": 0},
                store,
            )
        finally:
            await store.close()

    body = asyncio.run(_run())

    assert body["count"] == 1
    assert body["rows"][0]["signal"] == "mcp.transport.request.started"


def test_signal_events_star_wildcard_does_not_treat_underscore_as_wildcard() -> None:
    # C1/C2 integration: ``team_dispatch.*`` must match only the literal
    # ``team_dispatch`` family, not ``teamXdispatch`` (which a raw ``_`` LIKE
    # wildcard would falsely match).
    async def _run() -> dict[str, Any]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "team_dispatch.handoff",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 1000,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "mcp-server",
                        "payload": {},
                    },
                    {
                        "signal": "teamXdispatch.handoff",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 2000,
                        "timestamp": "2026-01-01T00:00:02Z",
                        "source": "mcp-server",
                        "payload": {},
                    },
                ]
            )
            return await _signal_events(
                {"signal": "team_dispatch.*", "limit": 10, "since_ts": 0},
                store,
            )
        finally:
            await store.close()

    body = asyncio.run(_run())

    assert body["count"] == 1
    assert body["rows"][0]["signal"] == "team_dispatch.handoff"


def test_signal_events_minutes_window_filters_old_events() -> None:
    # C1 integration: an explicit ``minutes`` window bounds results to the
    # recent past, excluding events older than the window.
    import time

    async def _run() -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "mcp.transport.request.started",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": now_ms - (60 * 60 * 1000),
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "mcp-server",
                        "payload": {"age": "old"},
                    },
                    {
                        "signal": "mcp.transport.request.started",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": now_ms - (60 * 1000),
                        "timestamp": "2026-01-01T01:00:01Z",
                        "source": "mcp-server",
                        "payload": {"age": "recent"},
                    },
                ]
            )
            return await _signal_events(
                {"signal": "mcp.transport.request.started", "limit": 10, "minutes": 5},
                store,
            )
        finally:
            await store.close()

    body = asyncio.run(_run())

    assert body["count"] == 1
    assert body["rows"][0]["payload"]["age"] == "recent"
