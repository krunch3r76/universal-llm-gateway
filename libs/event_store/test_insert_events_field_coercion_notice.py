"""Explicit-null field coercion emits one rate-limited notice per field."""

from __future__ import annotations

import pytest

from event_store import store as store_module
from event_store.store import EventStore, register_coerce_notice_hook


@pytest.mark.asyncio
async def test_one_coerced_field_increments_counter_once() -> None:
    notices: list[dict] = []
    register_coerce_notice_hook(
        notices.append,
        interval_sec=0.0,
    )
    store_module._coerce_notice_pending.clear()
    store_module._coerce_notice_last_emit_ts = 0.0
    event_store = EventStore(":memory:")
    await event_store.open()
    try:
        await event_store.insert_events(
            [
                {
                    "signal": "demo.null.role",
                    "role": None,
                    "scope": "global",
                    "ts_unix_ms": 1000,
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source": "producer-a",
                    "payload": {},
                }
            ]
        )
        store_module._maybe_emit_coerce_notice()
        assert len(notices) == 1
        assert notices[0]["signal"] == "events.coerced.ingest"
        assert notices[0]["field"] == "role"
        assert notices[0]["source"] == "producer-a"
        assert notices[0]["count"] == 1
    finally:
        register_coerce_notice_hook(None)
        await event_store.close()
