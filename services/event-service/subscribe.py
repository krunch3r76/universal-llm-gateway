"""WebSocket subscription handler — real-time event push.

Clients connect via WebSocket, send filter criteria, and receive matching
events as they arrive. Supports resume-from-seq for catching up after
reconnect (replays from SQLite then switches to live).

Per-connection bounded queue prevents slow clients from stalling the service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from .store import EventStore

logger = logging.getLogger(__name__)

_SUBSCRIBER_QUEUE_SIZE = 1000


def _matches_filter(event: dict[str, Any], filt: dict[str, str]) -> bool:
    """Check if an event matches a subscription filter.

    Filter keys map to event fields. Values support trailing wildcard
    (e.g. 'federation.*' matches 'federation.connection.established').
    """
    for key, pattern in filt.items():
        value = str(event.get(key, ""))
        if pattern.endswith("*"):
            if not value.startswith(pattern[:-1]):
                return False
        elif value != pattern:
            return False
    return True


async def websocket_handler(
    request: web.Request,
) -> web.WebSocketResponse:
    """Handle a WebSocket subscription connection.

    Protocol:
      Client sends: {"type": "subscribe", "filter": {...}, "resume_from": {"seq": N}}
      Server pushes: matching events as JSON messages
    """
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    store: EventStore = request.app["store"]
    subscriber_queues: set[asyncio.Queue[dict[str, Any]]] = request.app[
        "subscriber_queues"
    ]

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
    event_filter: dict[str, str] = {}
    subscriber_queues.add(queue)
    push_task: asyncio.Task[None] | None = None

    logger.info("Subscriber connected (%d total)", len(subscriber_queues))

    try:
        async for msg in ws:
            if msg.type != web.WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                await ws.send_json({"error": "Invalid JSON"})
                continue

            msg_type = data.get("type", "")

            if msg_type == "subscribe":
                event_filter = data.get("filter", {})
                resume_from = data.get("resume_from", {})
                resume_seq = resume_from.get("seq") if resume_from else None

                if resume_seq is not None:
                    rows = await store.query(
                        "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT 10000",
                        (resume_seq,),
                    )
                    for row in rows:
                        if not event_filter or _matches_filter(row, event_filter):
                            await ws.send_json(row)

                if push_task is not None:
                    push_task.cancel()
                    try:
                        await push_task
                    except asyncio.CancelledError:
                        pass
                push_task = asyncio.create_task(_push_loop(ws, queue, event_filter))
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "filter": event_filter,
                        "resumed_from": resume_seq,
                    }
                )
            else:
                await ws.send_json({"error": f"Unknown message type: {msg_type}"})

    except Exception as e:
        logger.warning("Subscriber error: %s", e)
    finally:
        if push_task is not None:
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass
        subscriber_queues.discard(queue)
        logger.info("Subscriber disconnected (%d remaining)", len(subscriber_queues))

    return ws


async def _push_loop(
    ws: web.WebSocketResponse,
    queue: asyncio.Queue[dict[str, Any]],
    event_filter: dict[str, str],
) -> None:
    """Continuously push matching events from queue to WebSocket."""
    try:
        while not ws.closed:
            event = await queue.get()
            if event_filter and not _matches_filter(event, event_filter):
                continue
            try:
                await ws.send_json(event)
            except (ConnectionResetError, RuntimeError):
                break
    except asyncio.CancelledError:
        pass
