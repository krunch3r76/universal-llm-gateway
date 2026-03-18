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

    Args:
        event: Event payload emitted by the store/fan-out path.
        filt: Filter mapping where keys are event fields and values are exact
            matches or trailing-wildcard prefixes.

    Returns:
        True when all filter keys match, otherwise False.

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
                raw_filter = data.get("filter", {})
                if isinstance(raw_filter, dict):
                    event_filter = {
                        str(k): str(v)
                        for k, v in raw_filter.items()
                        if isinstance(k, str)
                    }
                else:
                    event_filter = {}
                resume_from_raw = data.get("resume_from")
                resume_from = (
                    resume_from_raw if isinstance(resume_from_raw, dict) else None
                )
                resume_seq = resume_from.get("seq") if resume_from is not None else None

                async def _send_if_matches(item: dict[str, Any]) -> None:
                    if not event_filter or _matches_filter(item, event_filter):
                        await ws.send_json(item)

                if resume_seq is not None:
                    rows = await store.query(
                        "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT 10000",
                        (resume_seq,),
                    )
                    for row in rows:
                        await _send_if_matches(row)

                realtime_filter = event_filter.get("role") in (
                    None,
                    "realtime",
                )
                if realtime_filter:
                    for rt_ev in store.get_realtime_snapshot(limit=1000):
                        await _send_if_matches(rt_ev)

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
                        "resumed_from": resume_from,
                    }
                )
            else:
                await ws.send_json({"error": f"Unknown message type: {msg_type}"})

    except web.WebSocketError as e:
        logger.info("WebSocket closed unexpectedly: %s", e)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON received from subscriber: %s", e)
        await ws.send_json({"error": "Invalid JSON received"})
    except Exception as e:
        logger.error("Unhandled subscriber error: %s", e, exc_info=True)
        await ws.send_json({"error": "An unexpected server error occurred"})
    finally:
        if push_task is not None:
            push_task.cancel()
            try:
                await asyncio.wait_for(push_task, timeout=1.0)
            except asyncio.CancelledError:
                pass
            except TimeoutError:
                logger.warning("Push task did not terminate gracefully after cancellation")
            except Exception as e:
                logger.error("Error awaiting cancelled push task: %s", e, exc_info=True)
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
            except (ConnectionResetError, RuntimeError) as e:
                logger.info("Subscriber push connection error: %s", e)
                break
    except asyncio.CancelledError:
        pass
