"""Shared fixtures for the edge-op substrate tests.

Time is injected, never slept: ``ManualClock`` lets a test move past a lease
TTL or an op deadline instantly, so the whole suite runs in well under a
second and expiry is tested by its stored timestamps rather than by waiting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from systems.edge_ops.store import EdgeOpsJournal

LEASE_TTL_SECONDS = 30.0


class ManualClock:
    """A clock a test advances explicitly."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        """Move time forward and return the new value."""
        self.value += seconds
        return self.value


class RecordingEventBus:
    """Collects published events so a test can assert the vocabulary fired."""

    def __init__(self) -> None:
        self.signals: list[str] = []

    async def publish_nowait(self, event: object) -> None:
        """Record one published signal."""
        self.signals.append(getattr(event, "signal", ""))


@pytest.fixture
def clock() -> ManualClock:
    """An injectable clock starting at a fixed instant."""
    return ManualClock()


@pytest.fixture
def event_bus() -> RecordingEventBus:
    """An event bus that records instead of publishing."""
    return RecordingEventBus()


@pytest_asyncio.fixture
async def journal(
    tmp_path: Path,
    clock: ManualClock,
    event_bus: RecordingEventBus,
) -> AsyncIterator[EdgeOpsJournal]:
    """An initialized journal on a temporary path with time under test control."""
    store = EdgeOpsJournal(
        path=tmp_path / "edge-ops.db",
        clock=clock,
        event_bus=event_bus,
        lease_ttl_s=LEASE_TTL_SECONDS,
    )
    await store.initialize()
    yield store


async def seen_edge(store: EdgeOpsJournal, edge_id: str = "edge-a") -> None:
    """Make an edge present so ``admit`` does not fail fast."""
    await store.record_poll(edge_id=edge_id, kinds=("ping",))
