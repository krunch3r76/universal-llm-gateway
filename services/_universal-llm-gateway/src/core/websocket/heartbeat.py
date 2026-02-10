"""Periodic telemetry heartbeat publisher for WebSocket connections."""

import asyncio
from typing import Any

from universal_logging import get_logger

from .messages import create_telemetry_heartbeat_message

logger = get_logger(__name__)


class TelemetryHeartbeatPublisher:
    """
    Publishes periodic TELEMETRY_HEARTBEAT events to WebSocket connections.

    Proves telemetry pipeline is functioning without making capacity claims.
    Master uses these to distinguish "alive but idle" from "disconnected".

    Invariant: ∀ idle_period ≥ interval ⟹ ≥1 TELEMETRY_HEARTBEAT sent
    Invariant: ∀ heartbeat ⟹ ¬capacity_claim (no VRAM/RAM/model data)
    """

    def __init__(
        self,
        gateway_id: str,
        interval_seconds: float = 30.0,
    ):
        self._gateway_id = gateway_id
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False
        self._connections: set[Any] = set()  # WebSocket connections

    def register_connection(self, connection: Any) -> None:
        """Register a WebSocket connection for periodic heartbeats."""
        self._connections.add(connection)
        logger.debug(
            f"Registered connection for heartbeats (total: {len(self._connections)})"
        )

    def unregister_connection(self, connection: Any) -> None:
        """Unregister a WebSocket connection."""
        self._connections.discard(connection)
        logger.debug(
            f"Unregistered connection (remaining: {len(self._connections)})"
        )

    async def start(self) -> None:
        """Start periodic heartbeat publishing."""
        if self._running:
            logger.warning("Heartbeat publisher already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            f"✅ Started telemetry heartbeat publisher "
            f"(gateway={self._gateway_id}, interval={self._interval}s)"
        )

    async def stop(self) -> None:
        """Stop periodic heartbeat publishing."""
        if not self._running:
            return

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("Stopped telemetry heartbeat publisher")

    async def _heartbeat_loop(self) -> None:
        """Background loop that publishes heartbeats."""
        message = create_telemetry_heartbeat_message(self._gateway_id)

        while self._running:
            try:
                await asyncio.sleep(self._interval)

                if not self._connections:
                    continue

                # Broadcast heartbeat to all connections
                for conn in list(self._connections):
                    try:
                        await conn.send_json(message.to_dict())
                    except Exception as e:
                        logger.warning(
                            f"Failed to send heartbeat to connection: {e}"
                        )
                        # Connection will be removed on disconnect

                logger.debug(
                    f"📤 Sent TELEMETRY_HEARTBEAT to "
                    f"{len(self._connections)} connections"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
