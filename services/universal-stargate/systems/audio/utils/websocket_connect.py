"""WebSocket connection helper with Unix socket support."""

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import websockets
from universal_logging import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def connect_websocket(
    ws_url: str,
    socket_path: str | None = None,
    max_size: int | None = None,
    ping_interval: float | None = 20.0,
    ping_timeout: float | None = 30.0,
    close_timeout: float = 5.0,
    connect_timeout: float | None = None,
):
    """
    Connect to WebSocket with optional Unix socket transport.

    Args:
        ws_url: WebSocket URL (ws://host:port/path)
        socket_path: Unix socket path (if set, uses unix_connect)
        max_size: Maximum message size
        ping_interval: Ping interval in seconds
        ping_timeout: Ping timeout in seconds
        close_timeout: Close timeout in seconds
        connect_timeout: Connection timeout in seconds

    Yields:
        WebSocket connection
    """

    async def _connect():
        if socket_path:
            # Unix socket transport
            # Preserve path AND query string (required for audio params like model=...)
            parsed = urlparse(ws_url)
            uri = f"ws://localhost{parsed.path}"
            if parsed.query:
                uri = f"{uri}?{parsed.query}"

            logger.debug(f"Connecting via Unix socket: {socket_path} (uri={uri})")

            return await websockets.unix_connect(
                path=socket_path,
                uri=uri,
                max_size=max_size,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                close_timeout=close_timeout,
            )
        else:
            # TCP transport
            return await websockets.connect(
                ws_url,
                max_size=max_size,
                ping_interval=ping_interval,
                ping_timeout=ping_timeout,
                close_timeout=close_timeout,
            )

    # Apply timeout if specified
    if connect_timeout:
        ws = await asyncio.wait_for(_connect(), timeout=connect_timeout)
    else:
        ws = await _connect()

    try:
        yield ws
    finally:
        await ws.close()
