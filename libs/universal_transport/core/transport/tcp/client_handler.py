"""
TCP client handler for server connections.

This module handles individual client connections in AsyncTCPServer.
"""

import asyncio

from universal_logging import get_logger

from .connection import AsyncTCPReceiveError, AsyncTCPSendError

logger = get_logger(__name__)


class AsyncTCPClientHandler:
    """
    Handler for individual client connections in AsyncTCPServer.

    Provides the same interface as AsyncTCPTransport for consistency.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Initialize client handler."""
        self.reader = reader
        self.writer = writer
        self._closed = False

        # Get connection info for logging
        self.peername = writer.get_extra_info("peername")
        self.sockname = writer.get_extra_info("sockname")

    async def send(self, data: bytes) -> None:
        """Send data to client."""
        if self._closed:
            raise AsyncTCPSendError("Client connection closed")

        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            self._closed = True
            raise AsyncTCPSendError(f"Failed to send to client {self.peername}: {e}")

    async def receive(self, nbytes: int) -> bytes:
        """Receive exactly nbytes from client."""
        if self._closed:
            raise AsyncTCPReceiveError("Client connection closed")

        try:
            data = await self.reader.readexactly(nbytes)
            return data
        except asyncio.IncompleteReadError:
            self._closed = True
            raise AsyncTCPReceiveError(f"Client {self.peername} disconnected")
        except Exception as e:
            self._closed = True
            raise AsyncTCPReceiveError(
                f"Failed to receive from client {self.peername}: {e}"
            )

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

    def get_peer_address(self) -> tuple[str, int] | None:
        """Get client peer address."""
        return self.peername

    def get_local_address(self) -> tuple[str, int] | None:
        """Get local socket address."""
        return self.sockname
