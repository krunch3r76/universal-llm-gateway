"""
Load outcome tracking via Gateway WebSocket events.

Provides Future-based waiting for MODEL_LOADED or MODEL_LOAD_FAILED.

INVARIANT: Resolves on first matching event (loaded OR failed)
INVARIANT: Cleanup always runs (try/finally)

Architecture:
    Uses model-keyed callback registry in GatewayWebSocketClient.
    Multiple trackers can safely wait concurrently for different models.
    Each tracker registers for its specific routing_key, avoiding race conditions.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from gateway_websocket.ws_client.orchestrator import GatewayWebSocketClient

logger = get_logger(__name__)


class LoadOutcomeTracker:
    """
    Tracks model load outcome via WebSocket events.

    Resolves a Future when MODEL_LOADED or MODEL_LOAD_FAILED arrives
    for the target model (matched by routing_key).

    Concurrency-safe: Uses model-keyed callback registry instead of
    global callback replacement. Multiple trackers can safely wait
    for the same model simultaneously - all will be notified.

    Usage:
        tracker = LoadOutcomeTracker(model_id)
        tracker.register(ws_client)
        try:
            await asyncio.wait_for(tracker.future, timeout=180.0)
        finally:
            tracker.unregister()
    """

    def __init__(self, model_id: ModelId):
        self._model_id = model_id
        self._routing_key = model_id.routing_key
        self._future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._error: str | None = None
        self._ws_client: GatewayWebSocketClient | None = None
        self._registered = False

    @property
    def future(self) -> asyncio.Future[bool]:
        """Future that resolves when load completes or fails."""
        return self._future

    @property
    def error(self) -> str | None:
        """Error message if load failed, None otherwise."""
        return self._error

    def register(self, ws_client: GatewayWebSocketClient) -> None:
        """
        Register model-specific callbacks with WebSocket client.

        Uses model-keyed callback registry - safe for concurrent tracking.
        Multiple trackers can register for the same model; all will be notified.
        MUST call unregister() when done (use try/finally).

        Args:
            ws_client: Gateway WebSocket client
        """
        if self._registered:
            logger.warning(
                f"LoadOutcomeTracker already registered for {self._routing_key}"
            )
            return

        self._ws_client = ws_client

        # Register model-specific callbacks (no global callback manipulation)
        ws_client.register_model_load_callback(
            routing_key=self._routing_key,
            on_loaded=self._on_loaded,
            on_failed=self._on_failed,
        )

        self._registered = True
        logger.debug(f"LoadOutcomeTracker registered for {self._routing_key}")

    def unregister(self) -> None:
        """
        Unregister this tracker's specific callbacks.

        Safe to call multiple times. Does not affect other trackers
        waiting for the same model.
        """
        if not self._registered or self._ws_client is None:
            return

        # Unregister only our specific callbacks, not all for this routing_key
        self._ws_client.unregister_model_load_callback(
            routing_key=self._routing_key,
            on_loaded=self._on_loaded,
            on_failed=self._on_failed,
        )
        self._ws_client = None
        self._registered = False
        logger.debug(f"LoadOutcomeTracker unregistered for {self._routing_key}")

    async def _on_loaded(self, model_id_str: str, data: dict[str, Any]) -> None:
        """
        Handle MODEL_LOADED event for this model.

        Called only for events matching our routing_key (filtered by handler).
        """
        if self._future.done():
            return

        # Double-check routing_key match (defensive)
        try:
            loaded = ModelId.parse(model_id_str)
            if loaded.routing_key != self._routing_key:
                return
        except ValueError:
            logger.warning(
                f"LoadOutcomeTracker received unparseable model_id: {model_id_str}"
            )
            return

        logger.info(f"Load outcome: {model_id_str} loaded successfully")
        self._future.set_result(True)

    async def _on_failed(self, model_id_str: str, error: str) -> None:
        """
        Handle MODEL_LOAD_FAILED event for this model.

        Called only for events matching our routing_key (filtered by handler).
        """
        if self._future.done():
            return

        # Double-check routing_key match (defensive)
        try:
            failed = ModelId.parse(model_id_str)
            if failed.routing_key != self._routing_key:
                return
        except ValueError:
            logger.warning(
                f"LoadOutcomeTracker received unparseable model_id: {model_id_str}"
            )
            return

        logger.warning(f"Load outcome: {model_id_str} failed: {error}")
        self._error = error
        self._future.set_exception(LoadFailedError(error))


class LoadFailedError(Exception):
    """Raised when model load fails."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)
