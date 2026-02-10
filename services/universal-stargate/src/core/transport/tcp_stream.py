"""
Async TCP stream transport for reliable remote event delivery.

Pure async implementation - no threads, no locks.
"""

import asyncio
import json
from typing import Any

from universal_logging import get_logger

from .base import EventTransport

logger = get_logger(__name__)


class AsyncTCPStreamTransport(EventTransport):
    """
    Async TCP stream socket transport.

    Thread Safety: Not needed. All access from single async context.

    Features:
    - Reliable, ordered delivery
    - Multiple concurrent subscribers
    - Large payload support
    - Remote access (network-accessible)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.host = self.config.get("host", "0.0.0.0")
        self.port = self.config.get("port", 10000)
        self.server: asyncio.Server | None = None
        self.client_writers: list[asyncio.StreamWriter] = []

    async def start(self):
        """Start TCP server (async)."""
        if self._started:
            logger.warning(f"TCP transport already started on {self.host}:{self.port}")
            return

        try:
            self.server = await asyncio.start_server(
                self._handle_client,
                self.host,
                self.port,
                reuse_address=True,
            )
            self._started = True

            logger.info(f"✅ TCP transport started on {self.host}:{self.port}")

        except Exception as e:
            logger.error(f"❌ Failed to start TCP transport: {e}")
            self.enabled = False
            raise

    async def stop(self):
        """Stop TCP server and close all connections."""
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

        logger.info("TCP transport stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle new client connection (async callback)."""
        addr = writer.get_extra_info("peername")
        self.client_writers.append(writer)
        logger.info(
            f"📡 New TCP client from {addr} (total: {len(self.client_writers)})"
        )

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
            logger.debug(f"Client {addr} error: {e}")
        finally:
            self.client_writers.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(
                f"Client {addr} disconnected (remaining: {len(self.client_writers)})"
            )

    async def send_event(self, event_data: dict[str, Any]) -> bool:
        """Send event to all connected TCP clients (async)."""
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
                    f"Removed {len(disconnected)} disconnected TCP clients "
                    f"(remaining: {len(self.client_writers)})"
                )

            return success_count > 0

        except Exception as e:
            logger.error(f"Error in TCP send_event: {e}")
            return False


# Backward compatibility
TCPStreamTransport = AsyncTCPStreamTransport
