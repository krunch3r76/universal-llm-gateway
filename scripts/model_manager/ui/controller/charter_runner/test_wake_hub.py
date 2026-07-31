"""Unit tests for WakeHub dirty-set, mapper, resume seq, and subscribe degrade."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from libs.charter_runner_store.db import open_ledger_db
from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.wake_hub import (
    B3_SIGNAL_FILTERS,
    RESUME_SEQ_KEY,
    WakeDirtySet,
    WakeHub,
    WakeRootMapper,
    read_resume_seq,
    write_resume_seq,
)


@pytest.mark.offline
def test_b3_signal_filters_cover_wake_set() -> None:
    signals = {f["signal"] for f in B3_SIGNAL_FILTERS}
    assert "mcp.agentbus.turn.created" in signals
    assert "manage.charter.tick.resumed" in signals


@pytest.mark.offline
def test_dirty_set_coalesces_same_root() -> None:
    dirty = WakeDirtySet()
    for _ in range(10):
        dirty.enqueue("6171")
    batch = dirty.drain()
    assert batch == [("6171", 10)]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_mapper_turn_created_enrolled_only() -> None:
    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    mapper = WakeRootMapper(enrolled)
    await mapper.refresh_enrolled()
    ev = {
        "signal": "mcp.agentbus.turn.created",
        "payload": {"thread": "6171"},
    }
    assert mapper.map_event(ev, caps=CapStore()) == "6171"
    ev_other = {
        "signal": "mcp.agentbus.turn.created",
        "payload": {"thread": "9999"},
    }
    assert mapper.map_event(ev_other, caps=CapStore()) is None


@pytest.mark.offline
def test_resume_seq_persisted_in_ledger_meta(tmp_path) -> None:
    db = tmp_path / "ledger.sqlite"
    conn = open_ledger_db(db)
    try:
        assert read_resume_seq(conn=conn) == 0
        write_resume_seq(42, conn=conn)
        assert read_resume_seq(conn=conn) == 42
        row = conn.execute(
            "SELECT value FROM ledger_meta WHERE key = ?", (RESUME_SEQ_KEY,)
        ).fetchone()
        assert row is not None and row[0] == "42"
    finally:
        conn.close()


@pytest.mark.offline
@pytest.mark.asyncio
async def test_wake_hub_emits_tick_wake_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events_log: list[tuple[str, dict[str, Any]]] = []

    async def _capture(signal: str, payload: dict[str, Any], **_kw: Any) -> None:
        events_log.append((signal, payload))

    monkeypatch.setattr(
        "scripts.model_manager.observation_event_charter._emit", _capture
    )
    from scripts.model_manager import observation_event_charter as charter_events

    await charter_events.emit_manage_charter_tick_wake(
        root="6171",
        signal="mcp.agentbus.turn.created",
        coalesced_n=3,
    )
    assert events_log == [
        (
            "manage.charter.tick.wake",
            {
                "root": "6171",
                "signal": "mcp.agentbus.turn.created",
                "coalesced_n": 3,
            },
        )
    ]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_wake_hub_subscribe_degrade_does_not_crash() -> None:
    dirty = WakeDirtySet()
    wake_events: list[tuple[str, str, int]] = []

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    async def on_wake(root: str, signal: str, coalesced_n: int) -> None:
        wake_events.append((root, signal, coalesced_n))

    async def on_full() -> None:
        dirty.enqueue_many({"6171"})

    async def failing_subscribe(
        _filt: dict[str, str], _seq: int
    ) -> AsyncIterator[dict[str, Any]]:
        raise RuntimeError("subscribe unavailable")
        yield {}  # pragma: no cover

    hub = WakeHub(
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        caps=CapStore(),
        subscribe_events=failing_subscribe,
        on_wake=on_wake,
        on_full_roster_wake=on_full,
        backoff_s=0.01,
    )
    await hub.start()
    await asyncio.sleep(0.05)
    await hub.stop()
    assert wake_events == []
