"""
Unified event-driven eviction waiter for local and federated gateways.

Subscribes to MODEL_UNLOADED EventBus events (emitted by both local WebSocket
callbacks and FederatedGatewayManager telemetry handlers).

Path-agnostic: Works identically for local and federated evictions.

CRITICAL: Wait handles must be registered BEFORE HTTP requests are sent
to avoid race conditions where the event arrives before the waiter is ready.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import TYPE_CHECKING

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = get_logger(__name__)


class UnloadResult(Enum):
    """Result of unload wait operation."""

    UNLOADED = "unloaded"
    TIMEOUT = "timeout"
    FAILED = "failed"


class EvictionWaiter:
    """
    Event-driven waiter for model unload events.

    Subscribes to MODEL_UNLOADED events from EventBus (unified for local + federated).
    Provides async wait interface for eviction executors.

    Architecture:
    - Single EventBus subscription for all MODEL_UNLOADED events
    - Per-gateway+model wait handles (asyncio.Event)
    - Automatic cleanup of completed waits

    CRITICAL Race Condition Prevention:
    - Call register_wait() BEFORE sending HTTP unload request
    - Call wait_for_registered() AFTER HTTP request succeeds
    - This ensures the wait handle exists when the event arrives

    Usage:
        waiter = EvictionWaiter(event_bus)
        await waiter.start()

        # Register BEFORE HTTP request (prevents race)
        waiter.register_wait(gateway_name, model_id)

        # Send HTTP request
        result = await forwarder.forward_model_unload_request(...)

        # Wait AFTER HTTP request
        unload_result = await waiter.wait_for_registered(gateway_name, model_id)
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._pending_unloads: dict[tuple[str, str], asyncio.Event] = {}
        self._subscription_active = False
        self._handler_ref = None  # Keep reference for unsubscribe

    async def start(self) -> None:
        """Start listening for MODEL_UNLOADED events."""
        if self._subscription_active:
            return

        from src.scheduling.events import MODEL_UNLOADED

        self._handler_ref = self._on_model_unloaded
        self._event_bus.subscribe_async(MODEL_UNLOADED, self._handler_ref)
        self._subscription_active = True
        logger.debug("EvictionWaiter started, subscribed to MODEL_UNLOADED events")

    def stop(self) -> None:
        """Stop listening and wake all pending waiters."""
        if not self._subscription_active:
            return

        # Wake all pending waiters (treat as unloaded on shutdown)
        for event in self._pending_unloads.values():
            event.set()
        self._pending_unloads.clear()
        self._subscription_active = False
        logger.debug("EvictionWaiter stopped")

    async def _on_model_unloaded(self, event) -> None:
        """
        Handle MODEL_UNLOADED event from EventBus.

        Wakes any waiters for this gateway+model combination.

        Event payload:
            - gateway_name: Gateway identifier
            - model_id: Model that was unloaded (string)
        """
        if not self._subscription_active:
            return

        payload = event.payload
        gateway_name = payload.get("gateway_name")
        model_id_str = payload.get("model_id")

        if not gateway_name or not model_id_str:
            logger.warning(
                f"MODEL_UNLOADED event missing gateway_name or model_id: {payload}"
            )
            return

        # Normalize model_id for key lookup
        try:
            model_id = ModelId.parse(model_id_str)
            event_key = model_id.normalized
        except Exception as e:
            logger.warning(f"Failed to parse model_id {model_id_str}: {e}")
            return

        key = (gateway_name, event_key)
        wait_event = self._pending_unloads.get(key)

        if wait_event:
            logger.info(
                f"✅ MODEL_UNLOADED event received: {model_id_str} "
                f"on {gateway_name}, waking waiter"
            )
            wait_event.set()
            # Don't pop here - let wait_for_registered() clean up
        else:
            logger.debug(
                f"MODEL_UNLOADED event for {model_id_str} on {gateway_name} "
                f"(no waiter registered)"
            )

    def register_wait(self, gateway_name: str, model_id: ModelId) -> None:
        """
        Register a wait handle for model unload event.

        CRITICAL: Call this BEFORE sending HTTP unload request to prevent race.

        Args:
            gateway_name: Gateway identifier
            model_id: Model being unloaded
        """
        event_key = model_id.normalized
        key = (gateway_name, event_key)

        if key not in self._pending_unloads:
            self._pending_unloads[key] = asyncio.Event()
            logger.debug(
                f"Registered unload wait for {model_id} on {gateway_name} "
                f"(event_key={event_key})"
            )

    async def wait_for_registered(
        self,
        gateway_name: str,
        model_id: ModelId,
        timeout: float = 10.0,
    ) -> UnloadResult:
        """
        Wait for a previously registered unload event.

        CRITICAL: Call register_wait() BEFORE HTTP request, then this AFTER.

        Args:
            gateway_name: Gateway identifier
            model_id: Model being unloaded (must match register_wait call)
            timeout: Maximum seconds to wait (default 10s for force unload)

        Returns:
            UnloadResult.UNLOADED if event received, TIMEOUT otherwise
        """
        event_key = model_id.normalized
        key = (gateway_name, event_key)

        wait_event = self._pending_unloads.get(key)
        if wait_event is None:
            logger.error(
                f"No wait registered for {model_id} on {gateway_name}. "
                f"Call register_wait() before HTTP request!"
            )
            return UnloadResult.FAILED

        logger.info(
            f"⏳ Waiting for MODEL_UNLOADED event: {model_id} on {gateway_name} "
            f"(timeout={timeout}s)"
        )

        try:
            await asyncio.wait_for(wait_event.wait(), timeout=timeout)
            logger.info(f"✅ Unload confirmed via event: {model_id} on {gateway_name}")
            return UnloadResult.UNLOADED
        except TimeoutError:
            logger.warning(
                f"⏰ Timeout waiting for MODEL_UNLOADED event: {model_id} "
                f"on {gateway_name} after {timeout}s"
            )
            return UnloadResult.TIMEOUT
        finally:
            # Cleanup
            self._pending_unloads.pop(key, None)
