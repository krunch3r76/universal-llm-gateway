"""
Event broadcaster for debugging Universal Stargate event-driven architecture.

Broadcasts all events to Unix socket clients for real-time monitoring and debugging.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

from .event import Event

logger = get_logger(__name__)


@dataclass
class DebugClient:
    """Represents a connected debug client."""

    transport: SimpleTransportWrapper
    last_seen: float
    connected: bool = True


class UDSEventPublisher:
    """Non-blocking publisher that sends events to the event service over UDS.

    Auto-reconnects on connection loss (handles event service restarts).
    Buffers events locally when disconnected; flushes on reconnect.
    Fire-and-forget: publishing never blocks the event path.

    INVARIANT: ¬blocking_io_on_event_path
    INVARIANT: buffer_size ≤ maxsize (oldest dropped on overflow)
    """

    def __init__(
        self,
        socket_path: str,
        *,
        maxsize: int = 500,
        drop_notice_interval_sec: float = 1.0,
        source: str = "uds_event_publisher",
    ) -> None:
        self._socket_path = socket_path
        self._buffer_maxsize = maxsize
        self._buffer: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._flush_task: asyncio.Task[None] | None = None
        self._dropped = 0
        # Rate-limited publisher-side drop notice state. Mirrors the
        # server-side pattern in libs/event_store/ingest.py for symmetry.
        self._source = source
        self._drop_notice_interval_sec = drop_notice_interval_sec
        self._drop_notice_last_emit_ts: float = 0.0
        self._drop_notice_pending_count: int = 0
        self._drop_notice_last_signal: str = ""

    async def start(self) -> None:
        self._running = True
        self._flush_task = asyncio.create_task(self._connect_and_flush())
        logger.info("UDSEventPublisher started: %s", self._socket_path)

    async def stop(self) -> None:
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, asyncio.CancelledError) as e:
                logger.debug("Error waiting for writer to close: %s", e)
        logger.info("UDSEventPublisher stopped (dropped=%d)", self._dropped)

    def publish_nowait(self, event_dict: dict[str, Any]) -> None:
        """Queue an event for publishing (non-blocking, fire-and-forget)."""
        if not self._running:
            return
        line = json.dumps(event_dict) + "\n"
        try:
            self._buffer.put_nowait(line)
        except asyncio.QueueFull:
            try:
                self._buffer.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._buffer.put_nowait(line)
            except asyncio.QueueFull:
                pass
            self._dropped += 1
            self._drop_notice_pending_count += 1
            self._drop_notice_last_signal = str(event_dict.get("signal", ""))
            self._maybe_emit_drop_notice()

    def _maybe_emit_drop_notice(self) -> None:
        """Rate-limited ``publisher.events.dropped`` emission on buffer overflow.

        Scope is ``node``: publisher buffers are per-process and not meaningful
        when re-emitted on master. Emits at most one notice per
        ``_drop_notice_interval_sec``; drops in between aggregate into ``count``.
        Mirrors ``IngestServer._maybe_emit_drop_notice`` in
        ``libs/event_store/ingest.py``.

        Emission path: the notice is queued into ``_buffer`` like any event; if
        the buffer is still full (typical under sustained overload), one oldest
        user event is evicted to make room — same eviction pattern as
        ``publish_nowait``. One extra user event is lost per notice interval.
        """
        now = time.monotonic()
        if now - self._drop_notice_last_emit_ts < self._drop_notice_interval_sec:
            return

        count = self._drop_notice_pending_count
        if count <= 0:
            return

        self._drop_notice_last_emit_ts = now
        self._drop_notice_pending_count = 0

        notice: dict[str, Any] = {
            "signal": "publisher.events.dropped",
            "role": "coordination",
            "scope": "node",
            "timestamp": time.time(),
            "payload": {
                "count": count,
                "buffer_depth": self._buffer.qsize(),
                "buffer_max": self._buffer_maxsize,
                "signal_sample": self._drop_notice_last_signal,
                "source": self._source,
            },
            "source": self._source,
        }
        line = json.dumps(notice) + "\n"
        try:
            self._buffer.put_nowait(line)
        except asyncio.QueueFull:
            try:
                self._buffer.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._buffer.put_nowait(line)
            except asyncio.QueueFull:
                pass

    async def _connect_and_flush(self) -> None:
        """Background loop: connect to UDS, drain buffer, reconnect on failure."""
        while self._running:
            try:
                _, self._writer = await asyncio.open_unix_connection(self._socket_path)
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                await asyncio.sleep(2.0)
                continue

            try:
                while self._running:
                    try:
                        line = await asyncio.wait_for(self._buffer.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    self._writer.write(line.encode("utf-8"))
                    await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError, OSError) as e:
                logger.debug("UDS connection lost, reconnecting... Error: %s", e)
                if self._writer:
                    self._writer.close()
                    try:
                        await self._writer.wait_closed()
                    except Exception:
                        pass
                self._writer = None
                await asyncio.sleep(1.0)


class MinimalEventDebugBroadcaster:
    """
    Minimal event broadcaster for debugging event-driven architecture.

    Features:
    - Purely async implementation
    - Non-blocking event broadcasting
    - Unix socket transport (optional)
    - UDS publishing to event service (optional)
    - Graceful degradation when no clients connected
    - Automatic client connection management
    """

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        uds_publish_path: str | None = None,
    ):
        self.socket_path = socket_path
        self.debug_clients: list[DebugClient] = []
        self.server_socket: asyncio.Server | None = None
        self._running = False

        self._uds_publisher: UDSEventPublisher | None = None
        if uds_publish_path:
            self._uds_publisher = UDSEventPublisher(uds_publish_path)

    async def start_debug_server(self):
        """Start Unix socket server and UDS publisher."""
        self._running = True  # Set FIRST so broadcast_event works

        # Start socket server (optional)
        if self.socket_path:
            try:
                self.server_socket = await asyncio.start_unix_server(
                    self._handle_client_connection, self.socket_path
                )
                logger.info(f"🔍 Debug Events Server started on {self.socket_path}")
                # Start background cleanup task (only if socket enabled)
                asyncio.create_task(self._cleanup_disconnected_clients())
            except (OSError, ConnectionRefusedError) as e:
                logger.error(
                    f"❌ Failed to start debug events server on {self.socket_path}: {e}"
                )
            except Exception:
                logger.exception(
                    "❌ An unexpected error occurred while starting debug events server."
                )

        # Start UDS publisher to event service (independent of socket)
        if self._uds_publisher:
            await self._uds_publisher.start()

    async def _handle_client_connection(self, reader, writer):
        """Handle new debug client connection"""
        client = DebugClient(
            transport=SimpleTransportWrapper(reader, writer),
            last_seen=time.time(),
        )
        client.connected = True

        self.debug_clients.append(client)
        logger.info(
            f"🔍 Debug client connected. Total clients: {len(self.debug_clients)}"
        )

    async def broadcast_event(self, event: Any):
        """Broadcast event to socket clients and event service publisher.

        Args:
            event: The event object to broadcast. Can be an instance of
                `Event` or any other type.
        """
        if not self._running:
            return

        # Build debug_event dict
        if isinstance(event, Event):
            debug_event = {
                "type": "stargate_event",
                "signal": event.signal,
                "payload": event.payload,
                "role": event.role,
                "scope": event.scope,
                "timestamp": event.timestamp,
                "id": event.id,
                "source": "universal_stargate",
            }
        else:
            debug_event = {
                "type": "stargate_event",
                "signal": type(event).__name__,
                "payload": {"value": str(event)},
                "timestamp": time.time(),
                "source": "universal_stargate",
            }

        # Publish to event service UDS (non-blocking, fire-and-forget)
        if self._uds_publisher:
            self._uds_publisher.publish_nowait(debug_event)

        # Broadcast to socket clients (skip if no clients - secondary)
        if self.debug_clients:
            await self._broadcast_to_clients(debug_event)

    async def _broadcast_to_clients(self, event: dict[str, Any]):
        """Broadcast event to all connected clients (non-blocking)"""
        # Create tasks for all clients (non-blocking)
        tasks = []
        for client in self.debug_clients:
            if client.connected:
                task = asyncio.create_task(self._send_to_client(client, event))
                tasks.append(task)

        # Wait for all sends to complete (with short timeout)
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=0.5,  # 500ms timeout for entire broadcast
                )
            except TimeoutError:
                logger.debug("⚠️ Debug broadcast timeout - some clients may be slow")

    async def _send_to_client(self, client: DebugClient, event: dict[str, Any]):
        """Send event to specific client (with error handling)"""
        try:
            # Use a very short timeout to prevent blocking
            await asyncio.wait_for(
                client.transport.send(event),
                timeout=0.1,  # 100ms timeout per client
            )
            client.last_seen = time.time()
        except TimeoutError:
            logger.debug("⏰ Debug client timeout - marking as disconnected")
            client.connected = False
        except Exception as e:
            logger.warning(f"❌ Debug client error: {e}")
            client.connected = False

    async def _cleanup_disconnected_clients(self):
        """Periodically clean up disconnected clients"""
        while self._running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                current_time = time.time()
                active_clients = []

                for client in self.debug_clients:
                    # Remove clients that haven't been seen for 30s or are explicitly
                    # disconnected
                    if client.connected and (current_time - client.last_seen) < 30:
                        active_clients.append(client)
                    else:
                        logger.debug(
                            "🧹 Cleaning up disconnected debug client (connected=%s,"
                            "last_seen=%s)",
                            client.connected,
                            client.last_seen,
                        )

                self.debug_clients = active_clients

            except Exception as e:
                logger.exception(f"❌ Error in client cleanup: {e}")

    async def stop_debug_server(self):
        """Stop debug server and UDS publisher gracefully."""
        self._running = False

        # Stop UDS publisher
        if self._uds_publisher:
            await self._uds_publisher.stop()

        # Stop socket server
        if self.server_socket:
            self.server_socket.close()
            await self.server_socket.wait_closed()

        # Close client connections
        for client in self.debug_clients:
            try:
                await client.transport.close()
            except (OSError, asyncio.CancelledError) as e:
                logger.debug("Error closing client transport: %s", e)

        logger.info("🔍 Debug Events Server stopped")


class SimpleTransportWrapper:
    """Simple async transport wrapper for debug clients."""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self._connected = True

    async def send(self, message: dict[str, Any]) -> bool:
        """Send a message using JSONL framing."""
        if self.writer.is_closing():
            raise ConnectionError("Not connected: Writer is closing")

        try:
            # Add timestamp if not present
            if "timestamp" not in message:
                message["timestamp"] = time.time()

            # Serialize message as JSON with newline
            json_data = json.dumps(message) + "\n"

            # Send message
            self.writer.write(json_data.encode("utf-8"))
            await self.writer.drain()

            return True

        except Exception as e:
            raise ConnectionError(f"Send failed: {e}") from e

    async def close(self):
        """Close the transport connection."""
        if self.writer is None:
            return
        if self.writer.is_closing():
            return
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception as e:
            logger.warning("Error closing transport writer: %s", e)
