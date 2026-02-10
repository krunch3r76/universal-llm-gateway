"""
Unix socket client handler for server connections.

This module handles individual client connections in AsyncUnixServer.
"""

import asyncio

from universal_logging import get_logger

from .connection import AsyncUnixReceiveError, AsyncUnixSendError

logger = get_logger(__name__)


class AsyncUnixClientHandler:
    """
    Handler for individual client connections in AsyncUnixServer.

    Provides the same interface as AsyncUnixTransport for consistency.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Initialize client handler."""
        self.reader = reader
        self.writer = writer
        self._closed = False

    async def send(self, data: bytes) -> None:
        """Send data to client."""
        if self._closed:
            raise AsyncUnixSendError("Client connection closed")

        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            self._closed = True
            raise AsyncUnixSendError(f"Failed to send to client: {e}")

    async def receive(self, nbytes: int) -> bytes:
        """Receive exactly nbytes from client."""
        if self._closed:
            raise AsyncUnixReceiveError("Client connection closed")

        try:
            data = await self.reader.readexactly(nbytes)
            return data
        except asyncio.IncompleteReadError:
            self._closed = True
            raise AsyncUnixReceiveError("Client disconnected")
        except Exception as e:
            self._closed = True
            raise AsyncUnixReceiveError(f"Failed to receive from client: {e}")

    async def close(self) -> None:
        """Close client connection."""
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    async def wait_for_close(self) -> None:
        """Wait for client to close connection."""
        try:
            while not self._closed:
                # Try to read 1 byte to detect disconnection
                data = await self.reader.read(1)
                if not data:
                    break
        except Exception:
            pass
        finally:
            await self.close()

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return not self._closed
