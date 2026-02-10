"""WebSocket client for stream consumption over Unix sockets.

Provides StreamClient for connecting to /stream/{stream_id} endpoints
over Unix domain sockets, parsing SSE-formatted messages.

Example:
    >>> async with StreamClient(socket_path, stream_id) as client:
    ...     async for message in client.iter_messages():
    ...         print(message)

One-shot model: Connection failure = stream ended, no retry.

Ping Configuration:
    By default, pings are DISABLED (ping_interval=None, ping_timeout=None)
    to handle long time-to-first-token (TTFT) during inference. For 70B
    models, TTFT can exceed 60 seconds. Standard WebSocket ping defaults
    (20s interval, 20s timeout) would close the connection prematurely.
"""

from universal_logging import get_logger
from collections.abc import AsyncGenerator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from universal_protocol.sse.core import parse_sse

logger = get_logger(__name__)


class StreamClient:
    """WebSocket client for stream consumption over Unix socket.

    Implements async context manager for connection lifecycle.
    Automatically parses SSE-formatted messages into dicts.

    Design:
    - Uses httpx WebSocket support over Unix domain socket
    - One-shot model: connection failure = stream closed
    - Automatically parses SSE format and returns dicts
    - No reconnection logic

    Example:
        >>> async with StreamClient(socket_path, "stream-abc123") as client:
        ...     async for msg in client.iter_messages():
        ...         if msg["t"] == "token":
        ...             print(f"Token: {msg['txt']}")
        ...         elif msg["t"] == "done":
        ...             print(f"Complete: {msg['usage']}")
    """

    def __init__(
        self,
        socket_path: str,
        stream_id: str,
        timeout: float = 30.0,
        ping_interval: float | None = None,
        ping_timeout: float | None = None,
    ):
        """Initialize stream client.

        Args:
            socket_path: Path to Unix socket
                (e.g., "/tmp/universal-protocol/worker-1.sock")
            stream_id: Stream ID to connect to (e.g., "stream-abc123")
            timeout: Connection timeout in seconds (default: 30.0)
            ping_interval: Interval between keepalive pings in seconds.
                None disables pings (recommended for inference streams
                where TTFT can be 30-60+ seconds). Default: None.
            ping_timeout: Timeout waiting for pong response in seconds.
                None waits forever. Default: None (disabled).

        Raises:
            ValueError: If socket_path or stream_id invalid

        Note:
            Pings are disabled by default because inference streams may
            have long time-to-first-token (TTFT) periods where the
            server is busy generating but cannot respond to pings.
            The blocking generator iteration now runs in a thread pool,
            but disabling pings provides defense in depth.
        """
        if not socket_path or not isinstance(socket_path, str):
            raise ValueError(f"socket_path must be non-empty string, got {socket_path}")
        if not stream_id or not isinstance(stream_id, str):
            raise ValueError(f"stream_id must be non-empty string, got {stream_id}")

        self.socket_path = socket_path
        self.stream_id = stream_id
        self.timeout = timeout
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self._connection: websockets.WebSocketClientProtocol | None = None

    async def connect(self) -> None:
        """Connect to the stream endpoint.

        Opens WebSocket to /stream/{stream_id} over Unix socket.
        Pings are disabled by default to handle long TTFT during inference.

        Raises:
            websockets.exceptions.WebSocketException: If connection fails
        """
        try:
            # Build WebSocket URL
            ws_url = f"ws://localhost/stream/{self.stream_id}"

            # Open WebSocket connection over Unix socket
            # Disable pings by default - inference TTFT can be 30-60+ seconds
            self._connection = await websockets.unix_connect(
                path=self.socket_path,
                uri=ws_url,
                open_timeout=self.timeout,
                ping_interval=self.ping_interval,  # None = disabled
                ping_timeout=self.ping_timeout,  # None = disabled
            )
            logger.info(f"Connected to stream {self.stream_id}")

        except Exception:
            # Cleanup on connection failure
            if self._connection is not None:
                await self._connection.close()
                self._connection = None
            raise

    async def disconnect(self) -> None:
        """Disconnect from the stream endpoint.

        Idempotent: safe to call multiple times.
        """
        if self._connection is not None:
            try:
                await self._connection.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            self._connection = None

        logger.info(f"Disconnected from stream {self.stream_id}")

    async def iter_messages(self) -> AsyncGenerator[dict[str, Any], None]:
        """Iterate over incoming stream messages.

        Receives SSE-formatted messages, parses them, and yields dicts.

        Message types:
        - {"t": "token", "i": N, "txt": "..."}: Token data
        - {"t": "done", "usage": {...}}: Completion
        - {"t": "err", "code": "...", "message": "...", "source": "..."}: Error

        Yields:
            Parsed message dict

        Raises:
            ValueError: If message format is invalid
            RuntimeError: If not connected

        One-shot semantics: On any error or disconnection, stop iteration.

        Handles multiple SSE frames arriving buffered in a single
        WebSocket message.
        """
        if self._connection is None:
            raise RuntimeError("Not connected; call connect() first")

        try:
            # Buffer for incomplete SSE frames split across WebSocket messages
            incomplete_buffer = ""

            async for text in self._connection:
                # Append to buffer and split by SSE frame boundary (\n\n)
                combined = incomplete_buffer + text

                # Split by double newline to separate frames
                # Each SSE frame ends with \n\n
                frames = combined.split("\n\n")

                # Keep the last element (might be incomplete) in buffer
                # All complete frames are in frames[:-1]
                incomplete_buffer = frames[-1] if frames[-1] else ""

                # Process all complete frames
                for frame in frames[:-1]:
                    if frame.strip():  # Skip empty frames
                        try:
                            message = parse_sse(
                                frame + "\n\n"
                            )  # Add back frame boundary for parser
                            yield message
                            # logger.debug(f"Received message: {message}")
                        except ValueError as e:
                            logger.warning(f"Failed to parse SSE message: {e}")
                            # Skip malformed messages, continue reading
                            continue

        except ConnectionClosed:
            logger.info(f"Stream {self.stream_id} disconnected")
            return

        except Exception as e:
            logger.exception(f"Error reading from stream {self.stream_id}: {e}")
            raise

    async def __aenter__(self) -> "StreamClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def __repr__(self) -> str:
        """String representation."""
        connected = "connected" if self._connection is not None else "disconnected"
        return f"StreamClient(stream_id={self.stream_id}, {connected})"
