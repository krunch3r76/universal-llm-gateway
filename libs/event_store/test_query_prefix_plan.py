"""Prefix signal queries use idx_signal_ts and order by ts_unix_ms."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from event_store.operations_impl import _signal_events
from event_store.operations_trace import _federation_health
from event_store.store import EventStore


def _plan_detail(store: EventStore, sql: str, params: tuple[Any, ...]) -> str:
    rows = store._db.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()  # type: ignore[union-attr]
    return " | ".join(str(row[-1]) for row in rows)


async def _capture_query(
    store: EventStore, sql: str, params: tuple[Any, ...] = (), **kwargs: Any
) -> list[dict[str, Any]]:
    store._captured.append((sql, params))  # type: ignore[attr-defined]
    return await store._orig_query(sql, params, **kwargs)  # type: ignore[attr-defined]


@pytest.mark.offline
def test_prefix_queries_search_idx_signal_ts() -> None:
    """Prefix range plans search idx_signal_ts; they do not scan events."""

    async def _run() -> list[tuple[str, str]]:
        store = EventStore(":memory:")
        await store.open()
        store._captured = []  # type: ignore[attr-defined]
        store._orig_query = store.query  # type: ignore[attr-defined]
        store.query = _capture_query.__get__(store, EventStore)  # type: ignore[method-assign]
        try:
            await store.insert_events(
                [
                    {
                        "signal": "federation.member.health",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 2_000,
                        "timestamp": "2026-01-01T00:00:02Z",
                        "source": "federation",
                        "payload": {},
                    },
                    {
                        "signal": "mcp.transport.request.started",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 1_000,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "source": "mcp",
                        "payload": {},
                    },
                    {
                        "signal": "other.signal",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": 3_000,
                        "timestamp": "2026-01-01T00:00:03Z",
                        "source": "mcp",
                        "payload": {},
                    },
                ]
            )
            fed = await _federation_health({"limit": 10, "since_ts": 0}, store)
            sig = await _signal_events(
                {"signal": "mcp.transport.%", "limit": 10, "since_ts": 0},
                store,
            )
            assert [row["signal"] for row in fed["rows"]] == [
                "federation.member.health"
            ]
            assert [row["signal"] for row in sig["rows"]] == [
                "mcp.transport.request.started"
            ]
            plans: list[tuple[str, str]] = []
            for sql, params in store._captured:  # type: ignore[attr-defined]
                assert "LIKE" not in sql.upper()
                assert "ORDER BY ts_unix_ms DESC" in sql
                plans.append((sql, _plan_detail(store, sql, params)))
            return plans
        finally:
            await store.close()

    plans = asyncio.run(_run())
    assert len(plans) == 2
    for _sql, detail in plans:
        assert "idx_signal_ts" in detail
        assert "SCAN events" not in detail
