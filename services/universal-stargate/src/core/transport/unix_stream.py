"""
Async Unix stream socket transport for reliable local event delivery.

Pure async implementation - no threads, no locks.
"""

import asyncio
import json
import os
from typing import Any

from universal_logging import get_logger

from .base import EventTransport

logger = get_logger(__name__)


class AsyncUnixStreamTransport(EventTransport):
    """
    Async Unix domain stream socket transport.

    Thread Safety: Not needed. All access from single async context.

    Features:
    - Reliable, ordered delivery (stream socket)
    - Multiple concurrent subscribers
    - Large payload support (no UDP size limits)
    - Fast (no network stack overhead)
    - Local-only (same machine as proxy)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.socket_path = self.config.get("socket_path", "/tmp/stargate_events.sock")
        self.server: asyncio.Server | None = None
        self.client_writers: list[asyncio.StreamWriter] = []

    async def start(self):
        """Start Unix stream server (async)."""
        if self._started:
            logger.warning(
                f"Unix stream transport already started at {self.socket_path}"
            )
            return

        try:
            # Remove existing socket file
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)

            # Create async Unix domain socket server
            self.server = await asyncio.start_unix_server(
                self._handle_client,
                path=self.socket_path,
            )

            # Set permissions so any user can connect
            os.chmod(self.socket_path, 0o666)

            self._started = True
            logger.info(f"✅ Unix stream transport started at {self.socket_path}")

        except Exception as e:
            logger.error(f"❌ Failed to start Unix stream transport: {e}")
            self.enabled = False
            raise

    async def stop(self):
        """Stop Unix stream server and close all connections."""
        if not self._started:
            return

        self._started = False

        # Close all client connections
        for writer in self.client_writers:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.client_writers.clear()

        # Close server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

        # Remove socket file
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass

        logger.info("Unix stream transport stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle new client connection (async callback)."""
        self.client_writers.append(writer)
        logger.info(f"📡 New Unix stream client (total: {len(self.client_writers)})")

        try:
            # Keep connection open until client disconnects
            while self._started:
                try:
                    data = await asyncio.wait_for(reader.read(1), timeout=5.0)
                    if not data:
                        break  # Client disconnected
                except TimeoutError:
                    continue

        except Exception as e:
            logger.debug(f"Unix client error: {e}")
        finally:
            self.client_writers.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(
                f"Unix client disconnected (remaining: {len(self.client_writers)})"
            )

    async def send_event(self, event_data: dict[str, Any]) -> bool:
        """Send event to all connected clients (async)."""
        if not self.enabled or not self._started:
            return False

        try:
            message = json.dumps(event_data, separators=(",", ":"))
            message_bytes = (message + "\n").encode("utf-8")

            success_count = 0
            disconnected = []

            for writer in self.client_writers:
                try:
                    writer.write(message_bytes)
                    await writer.drain()
                    success_count += 1
                except Exception:
                    disconnected.append(writer)

            # Remove disconnected clients
            for writer in disconnected:
                self.client_writers.remove(writer)
                try:
                    writer.close()
                except Exception:
                    pass

            if disconnected:
                logger.info(
                    f"Removed {len(disconnected)} disconnected clients "
                    f"(remaining: {len(self.client_writers)})"
                )

            return success_count > 0

        except Exception as e:
            logger.error(f"Error in Unix stream send_event: {e}")
            return False


# Backward compatibility
UnixStreamTransport = AsyncUnixStreamTransport
