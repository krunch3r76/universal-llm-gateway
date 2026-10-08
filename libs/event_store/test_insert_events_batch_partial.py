"""Batch insert partial failure: rollback, row retry, and drop counting."""

from __future__ import annotations

import pytest

from event_store.store import EventStore, register_write_fail_hook


def _base_ev(signal: str, **extra: object) -> dict:
    return {
        "signal": signal,
        "role": "observation",
        "scope": "global",
        "ts_unix_ms": 1000,
        "timestamp": "2026-01-01T00:00:00Z",
        "source": "pytest",
        "payload": {},
        **extra,
    }


@pytest.mark.asyncio
async def test_insert_events_null_timestamp_coerces_and_keeps_batch() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        accepted = await store.insert_events(
            [
                _base_ev("good.a"),
                _base_ev("null.ts", timestamp=None),
                _base_ev("good.b"),
            ]
        )
        assert len(accepted) == 3
        rows = await store.query(
            "SELECT signal, timestamp FROM events ORDER BY seq", ()
        )
        assert rows[1]["signal"] == "null.ts"
        assert rows[1]["timestamp"] == ""
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_insert_events_dict_role_keeps_siblings_and_drops_one() -> None:
    store = EventStore(":memory:")
    await store.open()
    dropped: list[int] = []
    register_write_fail_hook(lambda count, _signals, _err: dropped.append(count))
    try:
        accepted = await store.insert_events(
            [
                _base_ev("good.a"),
                _base_ev("bad.dict.role", role={"nested": True}),
                _base_ev("good.b"),
            ]
        )
        assert [ev["signal"] for ev in accepted] == ["good.a", "good.b"]
        assert dropped == [1]
    finally:
        register_write_fail_hook(None)
        await store.close()


@pytest.mark.asyncio
async def test_insert_events_batch_failure_leaves_no_open_transaction() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        await store.insert_events(
            [
                _base_ev("pre.a"),
                _base_ev("pre.b"),
            ]
        )
        await store.insert_events(
            [
                _base_ev("mid.a"),
                _base_ev("mid.bad", role={"bad": True}),
                _base_ev("mid.b"),
            ]
        )
        rows = await store.query("SELECT signal FROM events ORDER BY seq", ())
        assert [r["signal"] for r in rows] == [
            "pre.a",
            "pre.b",
            "mid.a",
            "mid.b",
        ]
        assert "mid.bad" not in {r["signal"] for r in rows}
    finally:
        await store.close()
