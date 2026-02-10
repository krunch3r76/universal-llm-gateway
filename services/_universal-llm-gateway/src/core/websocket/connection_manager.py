"""WebSocket connection manager for Stargate clients."""

import asyncio
from typing import TYPE_CHECKING

from fastapi import WebSocket
from universal_logging import get_logger

from .heartbeat import TelemetryHeartbeatPublisher
from .messages import WebSocketMessage, create_ping_message
from .resource_telemetry import ResourceTelemetryPublisher

if TYPE_CHECKING:
    from ..resources.tracker import ResourceTracker

logger = get_logger(__name__)


class StargateConnectionManager:
    """Manages WebSocket connections from Stargate instances."""

    def __init__(self, ping_interval: float = 30.0):
        self._connections: set[WebSocket] = set()
        self._connection_tasks: dict[WebSocket, asyncio.Task] = {}
        self._ping_interval = ping_interval
        self._shutting_down = False

        # Telemetry heartbeat publisher
        # Note: gateway_id set during initialization at app startup
        self._heartbeat_publisher: TelemetryHeartbeatPublisher | None = None

        # Resource telemetry publisher
        # Note: resource_tracker set during initialization at app startup
        self._resource_telemetry_publisher: ResourceTelemetryPublisher | None = None

    def initialize_heartbeat(
        self, gateway_id: str, interval_seconds: float = 30.0
    ) -> None:
        """Initialize heartbeat publisher (called during app startup)."""
        self._heartbeat_publisher = TelemetryHeartbeatPublisher(
            gateway_id=gateway_id,
            interval_seconds=interval_seconds,
        )

    def initialize_resource_telemetry(
        self, resource_tracker: "ResourceTracker", interval_seconds: float = 5.0
    ) -> None:
        """Initialize resource telemetry publisher (called during app startup)."""
        self._resource_telemetry_publisher = ResourceTelemetryPublisher(
            resource_tracker=resource_tracker,
            interval_seconds=interval_seconds,
        )

    @property
    def connection_count(self) -> int:
        """Number of active connections."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self._connections.add(websocket)

        # Register for heartbeats if publisher initialized
        if self._heartbeat_publisher:
            self._heartbeat_publisher.register_connection(websocket)

        logger.debug(f"WebSocket connected, total: {len(self._connections)}")

    async def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        self._connections.discard(websocket)

        # Unregister from heartbeats
        if self._heartbeat_publisher:
            self._heartbeat_publisher.unregister_connection(websocket)

        if websocket in self._connection_tasks:
            self._connection_tasks[websocket].cancel()
            del self._connection_tasks[websocket]
        logger.debug(f"WebSocket disconnected, total: {len(self._connections)}")

    async def send_message(
        self, websocket: WebSocket, message: WebSocketMessage
    ) -> bool:
        """Send message to specific WebSocket. Returns False if send failed."""
        try:
            await websocket.send_json(message.to_dict())
            return True
        except Exception as e:
            logger.debug(f"Failed to send message to WebSocket: {e}")
            return False

    async def broadcast(self, message: WebSocketMessage) -> int:
        """Broadcast message to all connected Stargate instances.

        Returns:
            Number of successful sends.
        """
        if not self._connections:
            logger.warning(
                "⚠️ No WebSocket clients connected for broadcast "
                f"(message type: {message.type.value})"
            )
            return 0

        # Copy connections to avoid modification during iteration
        connections = list(self._connections)

        # Send to all connections in parallel
        async def safe_send(ws: WebSocket) -> bool:
            try:
                await ws.send_json(message.to_dict())
                return True
            except Exception:
                return False

        results = await asyncio.gather(
            *[safe_send(ws) for ws in connections], return_exceptions=True
        )

        success_count = sum(1 for r in results if r is True)

        if success_count < len(connections):
            logger.debug(
                f"Broadcast to {success_count}/{len(connections)} connections "
                f"(message type: {message.type.value})"
            )

        return success_count

    async def start_ping_loop(self, websocket: WebSocket) -> None:
        """Start ping/pong keep-alive loop for a connection."""

        async def ping_loop():
            try:
                while not self._shutting_down:
                    await asyncio.sleep(self._ping_interval)
                    ping_msg = create_ping_message()
                    if not await self.send_message(websocket, ping_msg):
                        logger.warning("Ping failed, connection may be dead")
                        break
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(ping_loop())
        self._connection_tasks[websocket] = task

    async def start_heartbeat_publisher(self) -> None:
        """Start periodic heartbeat publishing (called during startup)."""
        if self._heartbeat_publisher:
            await self._heartbeat_publisher.start()

    async def start_resource_telemetry_publisher(self) -> None:
        """Start periodic resource telemetry publishing (called during startup)."""
        if self._resource_telemetry_publisher:
            await self._resource_telemetry_publisher.start()

    async def shutdown(self) -> None:
        """Close all connections gracefully."""
        self._shutting_down = True

        # Stop resource telemetry publisher first
        if self._resource_telemetry_publisher:
            await self._resource_telemetry_publisher.stop()

        # Stop heartbeat publisher
        if self._heartbeat_publisher:
            await self._heartbeat_publisher.stop()

        # Cancel all ping tasks
        tasks_to_cancel = list(self._connection_tasks.values())
        for task in tasks_to_cancel:
            task.cancel()
        self._connection_tasks.clear()

        # Close all connections in parallel (best-effort)
        close_tasks = []
        for ws in list(self._connections):
            close_tasks.append(self._safe_close(ws))

        await asyncio.gather(*close_tasks, return_exceptions=True)
        self._connections.clear()

        logger.info("StargateConnectionManager shutdown complete")

    async def _safe_close(self, ws: WebSocket) -> None:
        """Safe close helper."""
        try:
            await ws.close()
        except Exception:
            pass


# Singleton instance
_manager: StargateConnectionManager | None = None


def get_connection_manager() -> StargateConnectionManager:
    """Get the singleton connection manager."""
    global _manager
    if _manager is None:
        _manager = StargateConnectionManager()
    return _manager
