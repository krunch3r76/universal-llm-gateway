"""Regression test: run_session_retention is role-aware (coordination survives)."""

from __future__ import annotations

import asyncio

from event_store.store import EventStore


def _ev(signal: str, role: str, ts: int) -> dict:
    return {
        "signal": signal,
        "role": role,
        "scope": "global",
        "ts_unix_ms": ts,
        "timestamp": "2026-01-01T00:00:00Z",
        "source": "mcp-server",
        "payload": {},
    }


def test_session_retention_preserves_coordination_deletes_observation() -> None:
    async def _run() -> dict[str, bool]:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    # Two session boundaries; max_sessions=2 → cutoff = ts 1000,
                    # deleting rows with ts < 1000.
                    _ev("event.service.started", "coordination", 1000),
                    _ev("event.service.started", "coordination", 3000),
                    # Pre-cutoff rows (ts=500): the contract under test.
                    _ev("mcp.request.started", "coordination", 500),
                    _ev("mcp.transport.request.started", "observation", 500),
                ]
            )
            await store.run_session_retention(2)
            rows = await store.query(
                "SELECT signal FROM events ORDER BY ts_unix_ms", ()
            )
            signals = {r["signal"] for r in rows}
            return {
                "coordination_survived": "mcp.request.started" in signals,
                "observation_deleted": "mcp.transport.request.started" not in signals,
            }
        finally:
            await store.close()

    result = asyncio.run(_run())
    assert result["coordination_survived"], "coordination row wrongly deleted"
    assert result["observation_deleted"], "observation row should be deleted"
