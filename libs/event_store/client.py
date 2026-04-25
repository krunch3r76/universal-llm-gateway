"""Async event subscription client for event_store.

Provides an async iterator over events matching a filter.
Connects via WebSocket to the Event Service query socket.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


async def subscribe_events(
    query_sock: str,
    *,
    filter: dict[str, str] | None = None,
    resume_from: int | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async iterator yielding events matching the filter.

    Args:
        query_sock: Path to the Event Service query UDS socket.
        filter: Event field filters (e.g. {"signal": "fleet.*"}).
        resume_from: Optional seq number to replay from.

    Yields:
        Event dicts as they arrive.
    """
    from websockets.asyncio.client import unix_connect

    async with unix_connect(
        query_sock,
        uri="ws://localhost/v1/subscribe",
        max_size=None,
    ) as ws:
        subscribe_msg: dict[str, Any] = {"type": "subscribe"}
        if filter:
            subscribe_msg["filter"] = filter
        if resume_from is not None:
            subscribe_msg["resume_from"] = {"seq": resume_from}
        await ws.send(json.dumps(subscribe_msg))

        async for raw in ws:
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue
