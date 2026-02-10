"""Query/response RPC over WebSocket."""

import asyncio
import json
import uuid
from typing import Any

from universal_logging import get_logger
from websockets.client import WebSocketClientProtocol

from ..messages import create_query_message

logger = get_logger(__name__)


class QueryManager:
    """
    Manages query/response RPC over WebSocket.

    Rare usage: most data is available from INIT + event-driven updates.
    Use for on-demand data that's not in INIT message.
    """

    def __init__(self) -> None:
        self._pending_queries: dict[str, asyncio.Future] = {}

    @property
    def pending_queries(self) -> dict[str, asyncio.Future]:
        """Mutable reference for QueryResponseHandler."""
        return self._pending_queries

    async def query(
        self,
        ws: WebSocketClientProtocol | None,
        query_type: str,
        params: dict[str, Any] | None = None,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """
        Send a query to Gateway and wait for response.

        Args:
            ws: WebSocket connection (must be non-None and open)
            query_type: Query type (e.g., "get_model_config")
            params: Query parameters
            timeout: Response timeout in seconds

        Returns:
            Response data

        Raises:
            RuntimeError: If websocket is None or closed
            TimeoutError: If no response within timeout

        Race-safe: captures ws parameter before sending to avoid race
        """
        # Race-safe: check ws before use (captures snapshot)
        if ws is None:
            raise RuntimeError("Not connected to Gateway (ws is None)")

        # Check if websocket is closed
        if ws.closed:
            raise RuntimeError("WebSocket connection is closed")

        request_id = str(uuid.uuid4())
        query_msg = create_query_message(query_type, params or {}, request_id)

        # Create future for response
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_queries[request_id] = future

        try:
            await ws.send(json.dumps(query_msg))
            return await asyncio.wait_for(future, timeout=timeout)
        except Exception as e:
            # Add context to any exception
            e.add_note(f"Query type: {query_type}, request_id: {request_id}")
            raise
        finally:
            self._pending_queries.pop(request_id, None)
