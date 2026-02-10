"""
Async Unix socket transport client for receiving monitoring events.

Pure async implementation - no threads, no locks.
"""

import asyncio
import os
import socket
import time
from collections.abc import Callable

# Import async universal_transport classes
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

try:
    from universal_transport.specialized import AsyncMonitoringClient

    ASYNC_MONITORING_AVAILABLE = True
except ImportError:
    ASYNC_MONITORING_AVAILABLE = False
    AsyncMonitoringClient = None

if TYPE_CHECKING:
    from .memory_backend import MemoryBackend

logger = get_logger(__name__)

# Constants
UNIX_SOCKET_PATH = "/tmp/stargate_events.sock"


class AsyncTransportClient:
    """
    Pure async Unix socket transport client.

    No threads, no locks - all operations in async context.

    Features:
    - Uses ONLY Unix socket communication
    - Automatic reconnection with exponential backoff
    - JSON stream parsing
    - Connection status monitoring

    Thread Safety: Not needed. All access from single async context.
    """

    def __init__(self, memory_backend: "MemoryBackend"):
        """Initialize async transport client.

        Args:
            memory_backend: MemoryBackend instance for storing events
        """
        self.memory_backend = memory_backend
        self.running = False
        self.status_callback: Callable[[str, str], None] | None = None

        # Async tasks (no threads)
        self._client_task: asyncio.Task | None = None
        self._connected = False

    async def connect(self) -> bool:
        """Establish connection via AsyncMonitoringClient.

        Returns:
            True if connection started, False otherwise
        """
        if not ASYNC_MONITORING_AVAILABLE:
            error_msg = "AsyncMonitoringClient not available"
            logger.error(error_msg)
            if self.status_callback:
                self.status_callback("error", error_msg)
            return False

        try:
            self.running = True

            # Wait for socket availability
            await self._wait_for_socket()

            # Start async client task
            self._client_task = asyncio.create_task(
                self._run_client(), name="AsyncTransportClient"
            )

            if self.status_callback:
                self.status_callback(
                    "connecting", "Connecting to Universal Stargate..."
                )

            logger.info(f"Starting async connection to {UNIX_SOCKET_PATH}")
            return True

        except Exception as e:
            error_msg = f"Connection failed: {e}"
            logger.error(error_msg)
            if self.status_callback:
                self.status_callback("error", error_msg)
            return False

    async def disconnect(self) -> None:
        """Close connection and clean up."""
        self.running = False

        if self._client_task:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            self._client_task = None

        self._connected = False

        if self.status_callback:
            self.status_callback("disconnected", "Disconnected from Universal Stargate")

        logger.info("Async transport client disconnected")

    def is_connected(self) -> bool:
        """Check connection status."""
        return self.running and self._connected

    def set_status_callback(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for connection status updates."""
        self.status_callback = callback

    async def _wait_for_socket(
        self, max_wait_time: float = 30.0, check_interval: float = 1.0
    ):
        """Wait for socket file to become available (async)."""
        start_time = time.time()
        logger.info(f"🔄 Waiting for socket: {UNIX_SOCKET_PATH}")

        while time.time() - start_time < max_wait_time:
            if not os.path.exists(UNIX_SOCKET_PATH):
                logger.debug("Socket not found, waiting...")
                await asyncio.sleep(check_interval)
                continue

            # Try to connect to validate
            try:
                test_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                test_sock.settimeout(2.0)
                test_sock.connect(UNIX_SOCKET_PATH)
                test_sock.close()
                logger.info("✅ Socket available and connectable")
                return
            except Exception:
                await asyncio.sleep(check_interval)

        raise ConnectionError(f"Socket not available after {max_wait_time}s")

    async def _run_client(self):
        """Run the async client with reconnection logic."""
        logger.info("Starting async monitoring client")

        max_message_size = 100 * 1024 * 1024  # 100MB
        retry_count = 0
        max_retries = 10
        backoff_time = 1.0

        while self.running and retry_count < max_retries:
            try:
                async with AsyncMonitoringClient(
                    unix_socket=UNIX_SOCKET_PATH, max_message_size=max_message_size
                ) as client:
                    logger.info(f"Connected to {UNIX_SOCKET_PATH}")
                    self._connected = True

                    if self.status_callback:
                        self.status_callback(
                            "connected", f"Connected to {UNIX_SOCKET_PATH}"
                        )

                    # Reset retry state on successful connection
                    retry_count = 0
                    backoff_time = 1.0

                    # Process events
                    while self.running:
                        try:
                            event = await client.receive_event(timeout=2.0)
                            if event:
                                self._handle_event(event)
                        except TimeoutError:
                            continue
                        except Exception as e:
                            logger.error(f"Error receiving event: {e}")
                            break

            except (ConnectionRefusedError, FileNotFoundError) as e:
                retry_count += 1
                self._connected = False
                logger.info(f"Socket not available ({retry_count}/{max_retries}): {e}")

                if self.status_callback:
                    self.status_callback(
                        "reconnecting", f"Retrying... ({retry_count}/{max_retries})"
                    )

                await asyncio.sleep(backoff_time)
                backoff_time = min(30.0, backoff_time * 1.5)

            except asyncio.CancelledError:
                logger.debug("Client task cancelled")
                raise

            except Exception as e:
                retry_count += 1
                self._connected = False
                logger.error(f"Connection error ({retry_count}/{max_retries}): {e}")

                if self.status_callback:
                    self.status_callback("reconnecting", "Error, retrying...")

                await asyncio.sleep(backoff_time)
                backoff_time = min(30.0, backoff_time * 1.5)

        if retry_count >= max_retries:
            logger.error(f"Failed after {max_retries} attempts")
            if self.status_callback:
                self.status_callback("error", f"Failed after {max_retries} attempts")

    def _handle_event(self, event: Any):
        """Handle monitoring event (synchronous - no await needed).

        Thread Safety: Not needed. Called from single async context.
        """
        try:
            event_data = {
                "type": getattr(event, "type", "unknown"),
                "data": getattr(event, "data", {}),
                "app_name": getattr(event, "app_name", "unknown"),
                "timestamp": getattr(event, "timestamp", time.time()),
            }

            # Merge nested data fields
            if isinstance(event_data["data"], dict):
                for key, value in event_data["data"].items():
                    if key not in ("type", "app_name", "timestamp"):
                        event_data[key] = value

            # Pass to memory backend (also in async context)
            self.memory_backend.add_event(event_data)

        except Exception as e:
            logger.error(f"Error handling event: {e}")


# Backward compatibility alias
TransportClient = AsyncTransportClient
