"""Resilient state channel with automatic reconnection and recovery."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import websockets
from universal_logging import get_logger
from websockets.exceptions import ConnectionClosed

from .protocol import MessageType

logger = get_logger(__name__)


class ResilientStateChannel:
    """State channel with automatic reconnection and state recovery.

    Extends base StateChannel with:
    - Automatic reconnection with exponential backoff
    - Subscription restoration after reconnect
    - Full state synchronization on reconnect
    - Configurable callbacks for state and metrics updates
    - Response routing for request/response patterns

    This provides a production-ready state channel client that handles
    network disruptions gracefully.
    """

    def __init__(
        self,
        channel_name: str,
        ws_url: str,
        on_state_update: Callable | None = None,
        on_metrics_update: Callable | None = None,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
    ):
        """Initialize resilient state channel.

        Args:
            channel_name: Name/ID for this channel (for logging)
            ws_url: WebSocket URL to connect to
            on_state_update: Callback for state updates (path, value)
            on_metrics_update: Callback for metrics updates
            reconnect_delay: Initial reconnect delay in seconds
            max_reconnect_delay: Maximum reconnect delay
        """
        self.channel_name = channel_name
        self.ws_url = ws_url
        self.on_state_update = on_state_update
        self.on_metrics_update = on_metrics_update

        # Connection state
        self._connection: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._receive_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None

        # Reconnection settings
        self._reconnect_delay = reconnect_delay
        self._initial_reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay

        # Track subscriptions for restoration
        self._subscriptions: set[str] = set()

        # State tracking
        self.local_state: dict[str, Any] = {}
        self.remote_state: dict[str, Any] = {}

        # For request/response pattern
        self.response_handler: Callable | None = None

    async def connect(self):
        """Connect to state channel with automatic reconnection."""
        self._running = True

        try:
            # Initial connection
            self._connection = await asyncio.wait_for(
                websockets.connect(self.ws_url), timeout=5.0
            )

            logger.info(f"✅ Connected to state channel '{self.channel_name}'")

            # Subscribe to patterns
            await self._restore_subscriptions()

            # Request initial sync
            await self._request_sync()

            # Start receive loop
            self._receive_task = asyncio.create_task(self._receive_loop())

        except Exception as e:
            logger.error(f"❌ Failed to connect to '{self.channel_name}': {e}")
            # Start reconnection
            if self._running:
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def disconnect(self):
        """Disconnect and cleanup."""
        logger.info(f"🔌 Disconnecting from '{self.channel_name}'")
        self._running = False

        # Cancel tasks
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # Close connection
        if self._connection:
            try:
                await self._connection.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            self._connection = None

    async def subscribe(self, pattern: str):
        """Subscribe to state updates matching pattern.

        Args:
            pattern: Subscription pattern (e.g., "gateways.*.resources.*")
        """
        if not self._connection:
            raise RuntimeError("Not connected")

        # Track subscription for reconnect
        self._subscriptions.add(pattern)

        message = {"type": MessageType.SUBSCRIBE.value, "pattern": pattern}
        await self._connection.send(json.dumps(message))
        logger.debug(f"Subscribed to pattern: {pattern}")

    async def request_sync(self):
        """Request full state synchronization."""
        if not self._connection:
            raise RuntimeError("Not connected")

        message = {"type": MessageType.SYNC_REQUEST.value}
        await self._connection.send(json.dumps(message))
        logger.debug("Requested state sync")

    async def send(self, data: dict):
        """Send message to remote endpoint.

        Args:
            data: Message data (dict)
        """
        if not self._connection:
            raise RuntimeError("Not connected")

        await self._connection.send(json.dumps(data))

    async def publish(self, path: str, value: Any):
        """Publish local state update.

        Args:
            path: Dot-separated path (e.g., "services.gateway.status")
            value: Value to publish
        """
        # Update local state
        self.local_state[path] = value

        # Create update message
        update = {
            "type": MessageType.STATE_UPDATE.value,
            "path": path,
            "value": value,
            "timestamp": time.time(),
        }

        # Send to remote
        await self.send(update)

    def get(self, path: str, default: Any = None) -> Any:
        """Get value from remote state.

        Args:
            path: State path
            default: Default value if not found

        Returns:
            State value or default
        """
        return self.remote_state.get(path, default)

    # Private methods

    async def _receive_loop(self):
        """Receive and process messages."""
        try:
            async for message in self._connection:
                if not self._running:
                    break

                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Invalid JSON from '{self.channel_name}': {e}")
                except Exception as e:
                    logger.error(
                        f"❌ Error handling message from '{self.channel_name}': {e}"
                    )

        except ConnectionClosed as e:
            logger.warning(f"⚠️ Connection closed for '{self.channel_name}': {e}")
        except Exception as e:
            logger.error(f"❌ Receive error for '{self.channel_name}': {e}")
        finally:
            self._running = False
            # Trigger reconnection
            if self._running and not self._reconnect_task:
                self._reconnect_task = asyncio.create_task(self._reconnect())

    async def _handle_message(self, data: dict):
        """Handle incoming message.

        Args:
            data: Parsed message data
        """
        msg_type = data.get("type")

        if msg_type == MessageType.STATE_UPDATE.value:
            # State update for a specific path
            path = data.get("path")
            value = data.get("value")

            # Update remote state
            self.remote_state[path] = value

            # Notify callback
            if self.on_state_update:
                await self.on_state_update(self.channel_name, path, value)

        elif msg_type == MessageType.SYNC_RESPONSE.value:
            # Full state sync
            state = data.get("state", {})
            logger.info(f"📋 Received full state sync from '{self.channel_name}'")

            # Update remote state
            self.remote_state = state

            # Process updates
            for path, value in state.items():
                if self.on_state_update:
                    await self.on_state_update(self.channel_name, path, value)

        elif msg_type == MessageType.HEARTBEAT.value:
            # Heartbeat - no action needed
            pass

        # Handle response messages (for request/response pattern)
        elif "result" in data or "error" in data:
            # This is a response to a request
            if self.response_handler:
                await self.response_handler(self.channel_name, data)

        else:
            logger.debug(f"Unknown message type: {msg_type}")

    async def _reconnect(self):
        """Attempt to reconnect with exponential backoff."""
        while self._running:
            logger.info(
                f"🔄 Attempting to reconnect to '{self.channel_name}' in {self._reconnect_delay}s..."
            )
            await asyncio.sleep(self._reconnect_delay)

            try:
                # Close existing connection if any
                if self._connection:
                    await self._connection.close()
                    self._connection = None

                # Attempt to reconnect
                self._connection = await asyncio.wait_for(
                    websockets.connect(self.ws_url), timeout=5.0
                )

                logger.info(f"✅ Reconnected to '{self.channel_name}'")

                # Restore subscriptions
                await self._restore_subscriptions()

                # Request full sync
                await self._request_sync()

                # Reset reconnect delay
                self._reconnect_delay = self._initial_reconnect_delay

                # Restart receive loop
                self._receive_task = asyncio.create_task(self._receive_loop())

                # Clear reconnect task
                self._reconnect_task = None

                break  # Exit reconnect loop

            except TimeoutError:
                logger.warning(f"⏱️ Reconnect timeout for '{self.channel_name}'")
                self._increase_reconnect_delay()
            except Exception as e:
                logger.error(f"❌ Reconnect failed for '{self.channel_name}': {e}")
                self._increase_reconnect_delay()

    def _increase_reconnect_delay(self):
        """Increase reconnect delay with exponential backoff."""
        self._reconnect_delay = min(
            self._reconnect_delay * 2, self._max_reconnect_delay
        )

    async def _restore_subscriptions(self):
        """Re-establish subscriptions after reconnect."""
        for pattern in self._subscriptions:
            try:
                message = {"type": MessageType.SUBSCRIBE.value, "pattern": pattern}
                await self._connection.send(json.dumps(message))
                logger.debug(f"🔔 Re-subscribed to pattern: {pattern}")
            except Exception as e:
                logger.error(f"Failed to re-subscribe to {pattern}: {e}")

    async def _request_sync(self):
        """Request full state sync."""
        try:
            message = {"type": MessageType.SYNC_REQUEST.value}
            await self._connection.send(json.dumps(message))
            logger.debug("📋 Requested full state sync")
        except Exception as e:
            logger.error(f"Failed to request sync: {e}")
