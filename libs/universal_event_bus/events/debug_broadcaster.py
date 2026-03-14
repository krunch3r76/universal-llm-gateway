"""
Event broadcaster for debugging Universal Stargate event-driven architecture.

Broadcasts all events to Unix socket clients for real-time monitoring and debugging.
"""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from .event import Event

logger = get_logger(__name__)


@dataclass
class DebugClient:
    """Represents a connected debug client."""

    transport: "SimpleTransportWrapper"
    last_seen: float
    connected: bool = True


class FileEventWriter:
    """
    Non-blocking file writer for event persistence with rotation.

    Architecture:
    - Events buffered to asyncio.Queue (zero-copy from event path)
    - Background task drains queue and writes to disk
    - File ops via run_in_executor (no event loop blocking)

    INVARIANT: ∀ event ⟹ eventually_written (async, non-blocking)
    INVARIANT: total_disk_usage ≤ max_file_size_mb × max_files
    INVARIANT: ¬blocking_io_on_event_path
    """

    def __init__(
        self,
        directory: str,
        *,
        signal_filter: str | None = None,
        max_file_size_mb: int = 50,
        max_files: int = 3,
        flush_interval_seconds: float = 1.0,
    ):
        self.directory = Path(directory)
        self._signal_filter = signal_filter
        self.max_file_size_bytes = max_file_size_mb * 1024 * 1024
        self.max_files = max_files
        self.flush_interval = flush_interval_seconds

        self._queue: asyncio.Queue[str] | None = None
        self._file_path: Path | None = None
        self._current_size = 0
        self._running = False
        self._writer_task: asyncio.Task | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="event_writer"
        )

    async def start(self) -> None:
        """Start the file writer."""
        loop = asyncio.get_event_loop()

        # Create queue (deferred from __init__ to avoid RuntimeError)
        self._queue = asyncio.Queue(maxsize=10000)

        # Create directory (blocking op in executor)
        await loop.run_in_executor(
            self._executor, lambda: self.directory.mkdir(parents=True, exist_ok=True)
        )

        self._file_path = self.directory / "current.jsonl"

        # Clear all event logs on startup
        def _clear_logs() -> None:
            if self._file_path and self._file_path.exists():
                self._file_path.unlink()
            for i in range(1, self.max_files + 1):
                old_file = self.directory / f"current.{i}.jsonl"
                if old_file.exists():
                    old_file.unlink()

        await loop.run_in_executor(self._executor, _clear_logs)
        self._current_size = 0

        self._running = True
        self._writer_task = asyncio.create_task(self._writer_loop())
        logger.info(f"FileEventWriter started: {self._file_path}")

    async def stop(self) -> None:
        """Stop the file writer and flush remaining events."""
        self._running = False

        # Signal writer to stop (only if queue exists)
        if self._queue:
            await self._queue.put("")  # Sentinel

        if self._writer_task:
            try:
                await asyncio.wait_for(self._writer_task, timeout=5.0)
            except TimeoutError:
                self._writer_task.cancel()

        self._executor.shutdown(wait=False)
        logger.info("FileEventWriter stopped")

    async def write_event(self, event: dict[str, Any]) -> None:
        """Queue an event for writing (non-blocking)."""
        if not self._running or not self._queue:
            return
        if self._signal_filter and not event.get("signal", "").startswith(
            self._signal_filter
        ):
            return

        json_line = json.dumps(event) + "\n"
        try:
            self._queue.put_nowait(json_line)
        except asyncio.QueueFull:
            # Drop oldest events if queue full (better than blocking)
            logger.debug("Event queue full, dropping event")

    async def _writer_loop(self) -> None:
        """Background task that drains queue and writes to disk."""
        if not self._queue:
            return

        loop = asyncio.get_event_loop()
        buffer: list[str] = []
        last_flush = asyncio.get_event_loop().time()

        while self._running or not self._queue.empty():
            try:
                # Wait for event with timeout
                try:
                    line = await asyncio.wait_for(
                        self._queue.get(), timeout=self.flush_interval
                    )
                    if line == "":  # Sentinel
                        break
                    buffer.append(line)
                except TimeoutError:
                    pass

                # Flush if interval elapsed or buffer large enough
                now = loop.time()
                if buffer and (
                    now - last_flush >= self.flush_interval or len(buffer) >= 100
                ):
                    await self._flush_buffer(buffer)
                    buffer = []
                    last_flush = now

            except Exception as e:
                logger.exception(f"Error in writer loop: {e}")

        # Final flush
        if buffer:
            await self._flush_buffer(buffer)

    async def _flush_buffer(self, lines: list[str]) -> None:
        """Flush buffered lines to disk (via executor)."""
        if not lines or not self._file_path:
            return

        loop = asyncio.get_event_loop()
        content = "".join(lines)
        content_size = len(content.encode("utf-8"))

        def _write_sync():
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(content)

        await loop.run_in_executor(self._executor, _write_sync)
        self._current_size += content_size

        # Check rotation
        if self._current_size >= self.max_file_size_bytes:
            await self._rotate()

    async def _rotate(self) -> None:
        """Rotate log files (via executor)."""
        loop = asyncio.get_event_loop()

        def _rotate_sync():
            # Delete oldest file if it would exceed max_files
            oldest_path = self.directory / f"current.{self.max_files}.jsonl"
            if oldest_path.exists():
                oldest_path.unlink()

            # Shift existing files (from max_files-1 down to 1)
            for i in range(self.max_files - 1, 0, -1):
                old_path = self.directory / f"current.{i}.jsonl"
                new_path = self.directory / f"current.{i + 1}.jsonl"
                if old_path.exists():
                    old_path.rename(new_path)

            # Move current to .1
            if self._file_path and self._file_path.exists():
                self._file_path.rename(self.directory / "current.1.jsonl")

        await loop.run_in_executor(self._executor, _rotate_sync)
        self._file_path = self.directory / "current.jsonl"
        self._current_size = 0
        logger.debug("Event log rotated")


class MinimalEventDebugBroadcaster:
    """
    Minimal event broadcaster for debugging event-driven architecture.

    Features:
    - Purely async implementation
    - Non-blocking event broadcasting
    - Unix socket transport (optional)
    - File persistence (default enabled)
    - Graceful degradation when no clients connected
    - Automatic client connection management
    """

    def __init__(
        self,
        socket_path: str | None = None,
        *,
        persistence_config: dict[str, Any] | None = None,
        pipeline_persistence_config: dict[str, Any] | None = None,
    ):
        self.socket_path = socket_path
        self.debug_clients: list[DebugClient] = []
        self.server_socket: asyncio.Server | None = None
        self._running = False

        # File persistence (can work independently of socket)
        self._file_writer: FileEventWriter | None = None
        if persistence_config and persistence_config.get("enabled"):
            self._file_writer = FileEventWriter(
                directory=persistence_config["directory"],
                signal_filter=persistence_config.get("signal_filter"),
                max_file_size_mb=persistence_config.get("max_file_size_mb", 50),
                max_files=persistence_config.get("max_files", 3),
                flush_interval_seconds=persistence_config.get(
                    "flush_interval_seconds", 1.0
                ),
            )

        self._pipeline_file_writer: FileEventWriter | None = None
        if pipeline_persistence_config and pipeline_persistence_config.get("enabled"):
            self._pipeline_file_writer = FileEventWriter(
                directory=pipeline_persistence_config["directory"],
                signal_filter=pipeline_persistence_config.get("signal_filter"),
                max_file_size_mb=pipeline_persistence_config.get(
                    "max_file_size_mb", 10
                ),
                max_files=pipeline_persistence_config.get("max_files", 2),
                flush_interval_seconds=pipeline_persistence_config.get(
                    "flush_interval_seconds", 0.5
                ),
            )

    async def start_debug_server(self):
        """Start Unix socket server and file writer."""
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
            except Exception as e:
                logger.error(f"❌ Failed to start debug events server: {e}")

        # Start file writer (independent of socket)
        if self._file_writer:
            await self._file_writer.start()
        if self._pipeline_file_writer:
            await self._pipeline_file_writer.start()

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
        """Broadcast event to socket clients AND write to file.

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

        # Write to file FIRST (always, if enabled) - primary use case
        if self._file_writer:
            await self._file_writer.write_event(debug_event)
        if self._pipeline_file_writer:
            await self._pipeline_file_writer.write_event(debug_event)

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
                    # Remove clients that haven't been seen for 30 seconds
                    if (current_time - client.last_seen) < 30:
                        active_clients.append(client)
                    else:
                        logger.debug("🧹 Cleaning up disconnected debug client")

                self.debug_clients = active_clients

            except Exception as e:
                logger.exception(f"❌ Error in client cleanup: {e}")

    async def stop_debug_server(self):
        """Stop debug server and file writer gracefully."""
        self._running = False

        # Stop file writer FIRST (capture final events)
        if self._file_writer:
            await self._file_writer.stop()
        if self._pipeline_file_writer:
            await self._pipeline_file_writer.stop()

        # Stop socket server
        if self.server_socket:
            self.server_socket.close()
            await self.server_socket.wait_closed()

        # Close client connections
        for client in self.debug_clients:
            try:
                await client.transport.close()
            except Exception:
                pass

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
            self._connected = False
            raise ConnectionError("Not connected: Writer is closing")
        if not self._connected:
            raise ConnectionError("Not connected")

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
            self._connected = False
            raise ConnectionError(f"Send failed: {e}") from e

    async def close(self):
        """Close the transport connection."""
        if not self._connected:
            return

        self._connected = False

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception as e:
            logger.warning(f"Error closing transport writer: {e}")
