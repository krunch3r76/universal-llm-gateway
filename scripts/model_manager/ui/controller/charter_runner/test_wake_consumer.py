"""Unit tests for WakeConsumer dirty-set drain and floor enqueue."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scripts.model_manager.ui.controller.charter_runner.admission import CapStore
from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
    WakeConsumer,
)
from scripts.model_manager.ui.controller.charter_runner.wake_hub import (
    WakeDirtySet,
    WakeRootMapper,
)


class _FakeTickLoop:
    def __init__(self) -> None:
        self._caps = CapStore()
        self._workspace_root = None
        self._on_admit = None
        self.passes: list[str] = []
        self.floor_batches: list[str] = []

    async def _tick_once(self) -> None:
        self.floor_batches.append("_tick_once")


@pytest.mark.offline
@pytest.mark.asyncio
async def test_consumer_drains_one_pass_per_root(monkeypatch: pytest.MonkeyPatch) -> None:
    tick = _FakeTickLoop()

    async def fake_batch(
        root_ids: list[str], *, tick_loop, wake_source: str
    ) -> Any:
        tick.passes.extend(root_ids)
        from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
            BatchOutcome,
        )

        return BatchOutcome(
            wake_source=wake_source,  # type: ignore[arg-type]
            roots_processed=len(root_ids),
            admitted=0,
            in_flight=0,
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        lambda: None,
    )

    dirty = WakeDirtySet()
    for _ in range(5):
        dirty.enqueue("6171")
    dirty.enqueue("6172")

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}, {"id": "6172"}]

    dirty._event.set()
    triggered = await dirty.wait(timeout=0.01)
    assert triggered
    batch = dirty.drain()
    assert sorted(r for r, _ in batch) == ["6171", "6172"]
    await fake_batch(
        [r for r, _ in batch],
        tick_loop=tick,  # type: ignore[arg-type]
        wake_source="wake",
    )
    assert tick.passes == ["6171", "6172"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_floor_path_runs_roots_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    tick = _FakeTickLoop()
    floor_sources: list[str] = []

    async def fake_batch(
        root_ids: list[str], *, tick_loop, wake_source: str
    ) -> Any:
        floor_sources.append(wake_source)
        from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
            BatchOutcome,
        )

        return BatchOutcome(
            wake_source=wake_source,  # type: ignore[arg-type]
            roots_processed=len(root_ids),
            admitted=0,
            in_flight=0,
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        lambda: None,
    )

    dirty = WakeDirtySet()

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    consumer = WakeConsumer(
        tick_loop=tick,  # type: ignore[arg-type]
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=0.01,
        services_healthy=lambda: True,
    )
    consumer._task = asyncio.create_task(consumer._run_loop())
    await asyncio.sleep(0.05)
    await consumer.stop()
    assert floor_sources
    assert all(source == "floor" for source in floor_sources)


@pytest.mark.offline
@pytest.mark.asyncio
async def test_floor_enqueues_all_enrolled_roots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bus-enrolled roots only — ledger ghosts do not expand the roster."""
    dirty = WakeDirtySet()

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}, {"id": "6172"}, {"id": "6173"}]

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.root_ledger."
        "list_open_work_item_root_ids",
        lambda *_a, **_k: set(),
    )

    mapper = WakeRootMapper(enrolled)
    consumer = WakeConsumer(
        tick_loop=_FakeTickLoop(),  # type: ignore[arg-type]
        dirty=dirty,
        mapper=mapper,
        floor_interval_s=0.01,
        services_healthy=lambda: True,
    )
    await consumer.enqueue_full_roster()
    batch = dirty.drain()
    assert {r for r, _ in batch} == {"6171", "6172", "6173"}


@pytest.mark.offline
@pytest.mark.asyncio
async def test_burst_coalesces_one_pass_per_root_per_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2 — >10 events/root/s burst yields ≤1 pass per root per drain."""
    tick = _FakeTickLoop()
    pass_counts: dict[str, int] = {}

    async def fake_batch(
        root_ids: list[str], *, tick_loop, wake_source: str
    ) -> Any:
        for root_id in root_ids:
            pass_counts[root_id] = pass_counts.get(root_id, 0) + 1
        tick.passes.extend(root_ids)
        from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
            BatchOutcome,
        )

        return BatchOutcome(
            wake_source=wake_source,  # type: ignore[arg-type]
            roots_processed=len(root_ids),
            admitted=0,
            in_flight=0,
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        lambda: None,
    )

    dirty = WakeDirtySet()
    for _ in range(15):
        dirty.enqueue("6171")
    for _ in range(12):
        dirty.enqueue("6172")

    batch = dirty.drain()
    assert len(batch) == 2
    await fake_batch(
        [r for r, _ in batch],
        tick_loop=tick,  # type: ignore[arg-type]
        wake_source="wake",
    )
    assert pass_counts == {"6171": 1, "6172": 1}
    assert tick.passes == ["6171", "6172"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_consumer_skips_drain_while_held(monkeypatch: pytest.MonkeyPatch) -> None:
    tick = _FakeTickLoop()
    dirty = WakeDirtySet()
    dirty.enqueue("6171")

    hold_reads = iter([None, object()])

    def read_hold():
        try:
            return next(hold_reads)
        except StopIteration:
            return None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        read_hold,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        AsyncMock(),
    )

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    consumer = WakeConsumer(
        tick_loop=tick,  # type: ignore[arg-type]
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=0.01,
        services_healthy=lambda: True,
    )
    consumer._task = asyncio.create_task(consumer._run_loop())
    await asyncio.sleep(0.05)
    await consumer.stop()
    assert tick.passes == []


@pytest.mark.offline
@pytest.mark.asyncio
async def test_resume_while_held_drains_within_hold_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dirty enqueued mid-hold-wait drains within hold_poll, not floor_interval."""
    tick = _FakeTickLoop()
    dirty = WakeDirtySet()
    hold_state = {"held": True}

    def read_hold():
        return object() if hold_state["held"] else None

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        read_hold,
    )

    async def fake_batch(
        root_ids: list[str], *, tick_loop, wake_source: str
    ) -> Any:
        tick.passes.extend(root_ids)
        from scripts.model_manager.ui.controller.charter_runner.wake_consumer import (
            BatchOutcome,
        )

        return BatchOutcome(
            wake_source=wake_source,  # type: ignore[arg-type]
            roots_processed=len(root_ids),
            admitted=0,
            in_flight=0,
        )

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        fake_batch,
    )

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    consumer = WakeConsumer(
        tick_loop=tick,  # type: ignore[arg-type]
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=1.0,
        hold_poll_s=0.05,
        services_healthy=lambda: True,
    )
    consumer._task = asyncio.create_task(consumer._run_loop())
    await asyncio.sleep(0.02)
    hold_state["held"] = False
    dirty.enqueue("6171")
    await asyncio.sleep(0.15)
    await consumer.stop()
    assert tick.passes == ["6171"]


@pytest.mark.offline
@pytest.mark.asyncio
async def test_start_enqueues_full_roster_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup calls enqueue_full_roster once for prompt first pass."""
    tick = _FakeTickLoop()
    dirty = WakeDirtySet()
    enqueue_calls = 0
    original_enqueue = WakeConsumer.enqueue_full_roster

    async def counting_enqueue(self: WakeConsumer) -> None:
        nonlocal enqueue_calls
        enqueue_calls += 1
        await original_enqueue(self)

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        lambda: None,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        AsyncMock(),
    )
    monkeypatch.setattr(WakeConsumer, "enqueue_full_roster", counting_enqueue)

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}, {"id": "6172"}]

    consumer = WakeConsumer(
        tick_loop=tick,  # type: ignore[arg-type]
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=0.01,
        hold_poll_s=0.01,
        services_healthy=lambda: True,
    )
    await consumer.start()
    await consumer.stop()
    assert enqueue_calls == 1


@pytest.mark.offline
@pytest.mark.asyncio
async def test_parked_with_dirty_set_does_not_busy_spin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parked branches must sleep — dirty.wait fast-path busy-spins when roots queued."""
    tick = _FakeTickLoop()
    dirty = WakeDirtySet()
    dirty.enqueue("6171")
    hold_polls = 0

    def read_hold():
        nonlocal hold_polls
        hold_polls += 1
        if hold_polls > 500:
            raise AssertionError(f"parked loop busy-spin: {hold_polls} hold polls")
        return object()

    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.tick_hold.read_hold",
        read_hold,
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.charter_runner.wake_consumer.run_roots_batch",
        AsyncMock(),
    )

    async def enrolled() -> list[dict[str, Any]]:
        return [{"id": "6171"}]

    consumer = WakeConsumer(
        tick_loop=tick,  # type: ignore[arg-type]
        dirty=dirty,
        mapper=WakeRootMapper(enrolled),
        floor_interval_s=1.0,
        hold_poll_s=0.02,
        services_healthy=lambda: True,
    )
    consumer._task = asyncio.create_task(consumer._run_loop())
    await asyncio.sleep(0.2)
    await consumer.stop()
    assert hold_polls < 20
