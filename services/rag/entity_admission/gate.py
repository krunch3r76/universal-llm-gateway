"""Fail-closed entity-admission gate.

One in-memory set of absolute paths that some entities.source_uri resolves to.
Built from a cortex-api REST snapshot, refreshed by a cortex.entity.source.changed
event (debounced full re-fetch) with a periodic backstop. Modeled on
services/rag/admission_gate/gate.py's configure→snapshot→subscribe lifecycle but
INVERTED to fail-closed: the capacity gate defaults OPEN (advisory); this gate's
membership test defaults to MISS (HOLD) — a correctness property, not advisory.
Do not conflate with the Stargate capacity admission_gate/ (different concern).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ._io import _backstop_loop, _dirty_refresh_worker, _refresh, _subscribe_loop
from ._signals import _apply_signal

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = logging.getLogger(__name__)


class EntityAdmissionGate:
    """Gate admission of files in entity-gated watch roots by entity backing.

    admitted_paths: absolute paths some entity points at via source_uri.
    is_admitted(p) is True iff p is in that set. The set defaults EMPTY, so
    before the first successful refresh every gated-root file is held (skip) —
    fail-closed. A miss is always transient: the reconcile loop re-attempts
    once a refresh succeeds (self-healing). Only Phase-1 structural corruption
    is a permanent skip.
    """

    def __init__(self, *, event_bus: EventBus | None = None) -> None:
        self._admitted: set[str] = set()
        self._ready: bool = False
        self._event_bus: EventBus | None = event_bus
        self._dirty: asyncio.Event = asyncio.Event()
        self._refresh_lock: asyncio.Lock = asyncio.Lock()
        self._last_seq: int | None = None
        self._tasks: list[asyncio.Task[None]] = []

    def is_admitted(self, abspath: str) -> bool:
        """True iff abspath is backed by some entity's source_uri (O(1))."""
        return abspath in self._admitted

    def is_ready(self) -> bool:
        """True once ≥1 snapshot/refresh has succeeded (fail-safe purge gate)."""
        return self._ready

    def snapshot_size(self) -> int:
        """Current admitted-path count (observability / fail-safe purge)."""
        return len(self._admitted)

    def mark_dirty(self) -> None:
        """Signal that a source.changed event arrived; worker re-fetches."""
        self._dirty.set()

    async def start(self) -> None:
        """Initial snapshot, then spawn subscriber + backstop + dirty worker.

        Idempotent. The startup snapshot is best-effort: cortex-api
        unavailability logs a warning and leaves the gate fail-closed (empty
        set, _ready=False); the backstop retries until it loads.
        """
        if self._tasks:
            return
        await _refresh(self)  # startup snapshot (best-effort)
        self._tasks = [
            asyncio.create_task(
                _subscribe_loop(self), name="rag-entity-gate-subscribe"
            ),
            asyncio.create_task(_backstop_loop(self), name="rag-entity-gate-backstop"),
            asyncio.create_task(
                _dirty_refresh_worker(self), name="rag-entity-gate-refresh"
            ),
        ]
        logger.info(
            "EntityAdmissionGate started (admitted=%d, ready=%s)",
            len(self._admitted),
            self._ready,
        )

    async def stop(self) -> None:
        """Cancel all background tasks. Idempotent."""
        if not self._tasks:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def _apply_signal(self, signal: str, payload: dict[str, object]) -> None:
        _apply_signal(self, signal, payload)
