"""Regression tests for correlation/taxonomy virtual columns in EventStore."""

from __future__ import annotations

import asyncio

from event_store.schema import migrate_correlation_taxonomy_columns
from event_store.store import EventStore


def test_correlation_taxonomy_migration_is_idempotent() -> None:
    async def _run() -> None:
        store = EventStore(":memory:")
        await store.open()
        try:
            await store.insert_events(
                [
                    {
                        "signal": "git_worker.dispatch.rejected",
                        "role": "observation",
                        "scope": "node",
                        "ts_unix_ms": 1000,
                        "timestamp": "2026-06-22T00:00:00Z",
                        "source": "git_worker",
                        "payload": {
                            "thread_id": "thread-1",
                            "dispatch_id": "req-1-aabbccdd",
                            "failure_layer": "validation",
                            "http_status": 422,
                            "worker_error_code": "CURSOR_PACKET_INVALID",
                        },
                    }
                ]
            )
            rows = await store.query(
                "SELECT thread_id, dispatch_id, failure_layer, http_status, "
                "worker_error_code FROM events WHERE dispatch_id = ?",
                ("req-1-aabbccdd",),
            )
            assert rows[0]["thread_id"] == "thread-1"
            assert rows[0]["failure_layer"] == "validation"
            assert rows[0]["http_status"] == 422
            assert store._db is not None
            migrate_correlation_taxonomy_columns(store._db)
            migrate_correlation_taxonomy_columns(store._db)
        finally:
            await store.close()

    asyncio.run(_run())
