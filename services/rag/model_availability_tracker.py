"""Aggregate model availability for RAG embedding and extraction paths.

Registers interest with Stargate via the admin watch endpoint and mirrors
coordination signals from the local EventBus so workers block on routing
admissibility rather than resident ``model.loaded`` telemetry alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from universal_event_bus import EventBus, Subscription

logger = logging.getLogger(__name__)

_STARGATE_BASE = "http://localhost:9999"
_client = httpx.AsyncClient(timeout=30.0)

_tracker: ModelAvailabilityTracker | None = None


class ModelAvailabilityTracker:
    """Track aggregate availability for a fixed set of Stargate model IDs.

    Watches are merged idempotently; the Stargate endpoint returns a snapshot
    used to seed initial availability before async events arrive. Subscriptions
    use exact signal names because the event bus may not support wildcards.
    """

    def __init__(self) -> None:
        self._bus: EventBus | None = None
        self._model_ids: set[str] = set()
        self._available: dict[str, bool] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._started = False
        self._subscriptions: list[Subscription] = []

    def _ensure_slot(self, model_id: str) -> asyncio.Event:
        if model_id not in self._events:
            self._available[model_id] = False
            ev = asyncio.Event()
            self._events[model_id] = ev
        return self._events[model_id]

    def _set_available(self, model_id: str, available: bool) -> None:
        self._ensure_slot(model_id)
        self._available[model_id] = available
        ev = self._events[model_id]
        if available:
            ev.set()
        else:
            ev.clear()

    async def start(self, bus: EventBus, model_ids: list[str]) -> None:
        """Register watch with Stargate, subscribe to bus, and seed from HTTP snapshot.

        Args:
            bus: RAG process EventBus (publishes to Event Service).
            model_ids: Model and pipeline IDs RAG must wait on for routing.

        Raises:
            RuntimeError: If HTTP watch registration fails completely.
        """
        if self._started:
            await self.stop()
        self._bus = bus
        for mid in model_ids:
            if mid:
                self._model_ids.add(mid)
                self._ensure_slot(mid)

        async def _on_available(event: Any) -> None:
            mid = event.payload.get("model_id")
            if isinstance(mid, str) and mid in self._model_ids:
                self._set_available(mid, True)

        async def _on_unavailable(event: Any) -> None:
            mid = event.payload.get("model_id")
            if isinstance(mid, str) and mid in self._model_ids:
                self._set_available(mid, False)

        self._subscriptions = [
            bus.subscribe_async("model.available", _on_available),
            bus.subscribe_async("model.unavailable", _on_unavailable),
        ]

        url = f"{_STARGATE_BASE}/api/v1/model-availability/watch"
        resp = await _client.post(
            url,
            json={"model_ids": sorted(self._model_ids)},
        )
        resp.raise_for_status()
        snap = resp.json()
        if isinstance(snap, dict):
            for mid, ok in snap.items():
                if mid in self._model_ids and isinstance(ok, bool):
                    self._set_available(mid, ok)

        self._started = True
        logger.info(
            "ModelAvailabilityTracker started for %s (snapshot=%s)",
            sorted(self._model_ids),
            snap,
        )

    async def stop(self) -> None:
        """Clear subscriptions; next start() re-registers."""
        self._started = False
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception as exc:
                logger.debug("subscription cleanup: %s", exc)
        self._subscriptions.clear()
        self._bus = None

    def is_available(self, model_id: str) -> bool:
        """Return last known aggregate availability for this model ID."""
        return bool(self._available.get(model_id, False))

    async def wait_until_available(self, model_id: str, timeout_s: float) -> bool:
        """Wait until model_id is aggregate-available or timeout.

        Returns:
            True if available before timeout, False otherwise.
        """
        if self.is_available(model_id):
            return True
        ev = self._ensure_slot(model_id)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
        except TimeoutError:
            return False
        return self.is_available(model_id)


def get_model_availability_tracker() -> ModelAvailabilityTracker | None:
    """Return the process singleton tracker if configured."""
    return _tracker


def set_model_availability_tracker(t: ModelAvailabilityTracker | None) -> None:
    """Set the process singleton (lifecycle owns creation)."""
    global _tracker
    _tracker = t


async def close_model_availability_client() -> None:
    """Close the shared HTTP client on RAG shutdown."""
    await _client.aclose()
