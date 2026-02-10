"""State channel implementation."""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import websockets
from universal_logging import get_logger
from websockets.exceptions import ConnectionClosed

from .protocol import MessageType, StateDelta, StateProtocol, StateUpdate

logger = get_logger(__name__)


@dataclass
class Subscription:
    """State subscription info."""

    pattern: str
    callback: Callable

    def matches(self, path: str) -> bool:
        """Check if path matches subscription pattern."""
        if self.pattern == "*":
            return True
        if self.pattern.endswith(".*"):
            prefix = self.pattern[:-2]
            return path.startswith(prefix)
        return path == self.pattern


class StateChannel:
    """Bidirectional state synchronization channel."""

    def __init__(self, socket_path: str, endpoint: str = "/state"):
        """Initialize state channel.

        Args:
            socket_path: Path to Unix socket
            endpoint: WebSocket endpoint path (default: /state)
        """
        self.socket_path = socket_path
        self.endpoint = endpoint
        self._connection: websockets.WebSocketClientProtocol | None = None
        self.local_state: dict[str, Any] = {}
        self.remote_state: dict[str, Any] = {}
        self.subscriptions: list[Subscription] = []
        self.version = 0
        self._running = False
        self._sync_task = None
        self._receive_task = None

    async def connect(self):
        """Connect to remote state channel."""
        try:
            # Build WebSocket URL
            ws_url = f"ws://localhost{self.endpoint}"

            # Open WebSocket connection over Unix socket
            self._connection = await websockets.unix_connect(
                path=self.socket_path,
                uri=ws_url,
            )

            # Request full sync
            await self._request_sync()

            # Start background tasks
            self._running = True
            self._sync_task = asyncio.create_task(self._sync_loop())
            self._receive_task = asyncio.create_task(self._receive_loop())

            logger.info("State channel connected")

        except Exception:
            # Cleanup on connection failure
            if self._connection is not None:
                await self._connection.close()
                self._connection = None
            raise

    async def disconnect(self):
        """Disconnect from state channel."""
        self._running = False

        # Cancel background tasks
        if self._sync_task:
            self._sync_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()

        # Close connection
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as e:
                logger.warning(f"Error closing connection: {e}")
            self._connection = None

    async def subscribe(self, pattern: str, callback: Callable):
        """Subscribe to state changes matching pattern."""
        sub = Subscription(pattern, callback)
        self.subscriptions.append(sub)

        # Send subscription to remote
        await self._send({"type": MessageType.SUBSCRIBE.value, "pattern": pattern})

    async def publish(self, path: str, value: Any):
        """Publish local state update."""
        # Update local state
        self._set_nested(self.local_state, path, value)
        self.version += 1

        # Create update
        update = StateUpdate(
            path=path, value=value, timestamp=time.time(), version=self.version
        )

        # Send to remote
        await self._send(StateProtocol.encode_update(update))

        # Notify local subscribers
        await self._notify_subscribers(path, value)

    def get(self, path: str, default: Any = None) -> Any:
        """Get value from remote state."""
        return self._get_nested(self.remote_state, path, default)

    async def wait_for(self, path: str, condition: Callable, timeout: float = 30):
        """Wait for state condition to be met."""
        event = asyncio.Event()
        result = None

        async def check_condition(path_arg, value):
            nonlocal result
            if condition(value):
                result = value
                event.set()

        # Subscribe temporarily
        await self.subscribe(path, check_condition)

        # Check current state
        current = self.get(path)
        if current and condition(current):
            return current

        # Wait for condition
        try:
            await asyncio.wait_for(event.wait(), timeout)
            return result
        except TimeoutError:
            return None

    async def _handle_message(self, message: dict[str, Any]):
        """Handle incoming state message."""
        try:
            decoded = StateProtocol.decode_message(message)

            if isinstance(decoded, StateUpdate):
                # Update remote state
                self._set_nested(self.remote_state, decoded.path, decoded.value)

                # Notify subscribers
                await self._notify_subscribers(decoded.path, decoded.value)

            elif isinstance(decoded, StateDelta):
                # Apply delta to remote state
                self._apply_delta(decoded)

                # Notify subscribers
                value = self.get(decoded.path)
                await self._notify_subscribers(decoded.path, value)

            elif message.get("type") == MessageType.SYNC_RESPONSE.value:
                # Full state sync
                self.remote_state = message.get("state", {})
                self.version = message.get("version", 0)

        except Exception as e:
            logger.error(f"Error handling state message: {e}")

    async def _notify_subscribers(self, path: str, value: Any):
        """Notify matching subscribers."""
        for sub in self.subscriptions:
            if sub.matches(path):
                try:
                    await sub.callback(path, value)
                except Exception as e:
                    logger.error(f"Subscriber error: {e}")

    async def _send(self, message: dict[str, Any]):
        """Send message over WebSocket."""
        if self._connection is None:
            raise RuntimeError("Not connected")
        await self._connection.send(json.dumps(message))

    async def _request_sync(self):
        """Request full state sync from remote."""
        await self._send(
            {"type": MessageType.SYNC_REQUEST.value, "version": self.version}
        )

    async def _receive_loop(self):
        """Receive and process incoming messages."""
        if self._connection is None:
            raise RuntimeError("Not connected")

        try:
            async for text in self._connection:
                try:
                    message = json.loads(text)
                    await self._handle_message(message)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode message: {e}")
                except Exception as e:
                    logger.error(f"Error handling message: {e}")

        except ConnectionClosed:
            logger.info("State channel connection closed")
            self._running = False
        except Exception as e:
            logger.error(f"Receive loop error: {e}")
            self._running = False

    async def _sync_loop(self):
        """Periodic sync and heartbeat."""
        while self._running:
            try:
                # Send heartbeat
                await self._send(
                    {"type": MessageType.HEARTBEAT.value, "timestamp": time.time()}
                )

                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Sync loop error: {e}")
                await asyncio.sleep(5)

    def _get_nested(self, data: dict, path: str, default: Any = None):
        """Get nested value by dot-separated path."""
        keys = path.split(".")
        current = data

        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default

        return current

    def _set_nested(self, data: dict, path: str, value: Any):
        """Set nested value by dot-separated path."""
        keys = path.split(".")
        current = data

        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

    def _apply_delta(self, delta: StateDelta):
        """Apply delta operation to state."""
        if delta.operation == "set":
            self._set_nested(self.remote_state, delta.path, delta.value)
        elif delta.operation == "delete":
            # Remove the key
            keys = delta.path.split(".")
            current = self.remote_state

            for key in keys[:-1]:
                if key in current:
                    current = current[key]
                else:
                    return

            if keys[-1] in current:
                del current[keys[-1]]
        elif delta.operation == "append":
            # Append to a list at the given path
            current = self._get_nested(self.remote_state, delta.path, [])
            if isinstance(current, list):
                current.append(delta.value)
                self._set_nested(self.remote_state, delta.path, current)
