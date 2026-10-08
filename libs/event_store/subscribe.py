"""WebSocket subscription handler - real-time event push.

Clients connect via WebSocket, send filter criteria, and receive matching
events as they arrive. Supports resume-from-seq for catching up after
reconnect (replays from SQLite then switches to live).

Per-connection bounded queue prevents slow clients from stalling the service.
The queue stays in the fan-out set only while this handler is running. A peer
that dies during replay is not in ``receive`` yet, so a dedicated reader
watches for disconnect and cancels the in-flight send; ``finally`` then drops
the queue. Slow live clients still overflow in place.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .store import EventStore

logger = logging.getLogger(__name__)

_DEFAULT_SUBSCRIBER_QUEUE_SIZE = 1000
# Page size only — never a replay cap. Catch-up walks until the window is empty.
_REPLAY_PAGE_SIZE = 10000


class LiveSubscribers:
    """Open subscribe sockets dropped on shutdown without waiting for peers.

    Each handler registers the ``disconnected`` event it already waits on.
    ``close_all`` sets those events so receive and push loops return, then
    waits until ``finally`` unregisters. A peer that never reads is not
    consulted; query-server abort covers a handler that misses the wait.
    """

    def __init__(self) -> None:
        self._events: set[asyncio.Event] = set()

    def register(self, disconnected: asyncio.Event) -> None:
        """Remember one live handler so shutdown can unblock its receive wait."""
        self._events.add(disconnected)

    def discard(self, disconnected: asyncio.Event) -> None:
        """Forget a handler after its ``finally`` has dropped the queue."""
        self._events.discard(disconnected)

    async def close_all(self, *, timeout: float) -> None:
        """Set every disconnect event and wait until handlers unregister.

        ``timeout`` bounds the wait. Handlers still registered when it elapses
        are left for the query-server peer abort that follows.
        """
        for event in list(self._events):
            event.set()
        deadline = time.monotonic() + timeout
        while self._events and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        if self._events:
            logger.warning(
                "shutdown left %d subscriber handler(s) after %.1fs",
                len(self._events),
                timeout,
            )


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


async def _send_json_or_dead(
    ws: WebSocket,
    payload: Any,
    disconnected: asyncio.Event,
) -> bool:
    """Send one frame. Return False when the peer is gone or the send fails.

    The send runs as its own task so a peer close can cancel it. Uvicorn's
    ``send`` waits on ``writable`` and does not set that event from
    ``connection_lost`` after ``pause_writing``, so a bare ``await send_json``
    during replay never returns and ``finally`` never drops the queue.
    """
    if disconnected.is_set():
        return False
    send_task = asyncio.create_task(ws.send_json(payload))
    stop_task = asyncio.create_task(disconnected.wait())
    try:
        done, _pending = await asyncio.wait(
            {send_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        send_task.cancel()
        stop_task.cancel()
        raise
    if send_task not in done:
        send_task.cancel()
        with contextlib.suppress(
            asyncio.CancelledError, RuntimeError, WebSocketDisconnect
        ):
            await send_task
        return False
    stop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stop_task
    try:
        send_task.result()
    except (RuntimeError, WebSocketDisconnect, OSError):
        disconnected.set()
        return False
    return True


async def _send_if_matches(
    ws: WebSocket,
    item: dict[str, Any],
    event_filter: dict[str, str],
    disconnected: asyncio.Event,
) -> bool:
    if event_filter and not _matches_filter(item, event_filter):
        return True
    return await _send_json_or_dead(ws, item, disconnected)


async def _read_client_frames(
    ws: WebSocket,
    incoming: asyncio.Queue[str],
    disconnected: asyncio.Event,
) -> None:
    """Own ``ws.receive`` so a disconnect is observed during replay sends."""
    try:
        while not disconnected.is_set():
            message = await ws.receive()
            kind = message["type"]
            if kind == "websocket.disconnect":
                disconnected.set()
                return
            if kind == "websocket.receive":
                text = message.get("text")
                if isinstance(text, str):
                    await incoming.put(text)
    except asyncio.CancelledError:
        raise
    except (RuntimeError, WebSocketDisconnect):
        disconnected.set()


async def _next_client_text(
    incoming: asyncio.Queue[str],
    disconnected: asyncio.Event,
) -> str:
    get_task = asyncio.create_task(incoming.get())
    stop_task = asyncio.create_task(disconnected.wait())
    try:
        done, _pending = await asyncio.wait(
            {get_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        get_task.cancel()
        stop_task.cancel()
        raise
    if disconnected.is_set():
        if get_task in done and not get_task.cancelled():
            get_task.result()
        else:
            get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await get_task
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        raise WebSocketDisconnect
    stop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stop_task
    return get_task.result()


async def _cancel_task(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def create_subscribe_router(
    store: EventStore,
    subscriber_queues: set[asyncio.Queue[dict[str, Any]]],
    *,
    subscriber_queue_maxsize: int = _DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    live_subscribers: LiveSubscribers | None = None,
) -> APIRouter:
    """Build a FastAPI router for WebSocket subscriptions.

    Args:
        subscriber_queue_maxsize: Per-connection bounded queue depth. Slow
            clients whose queue fills trigger subscriber-side overflow (oldest
            event evicted + ``events.dropped.subscribe`` notice). Tune upward
            for bursty broadcast workloads; tune downward to shed slow consumers
            faster. A dead peer is removed from the set; a full queue alone is
            not.
        live_subscribers: When set, shutdown calls ``close_all`` on this same
            object to drop sockets without waiting for the peer to disconnect.
    """
    tracker = live_subscribers if live_subscribers is not None else LiveSubscribers()
    router = APIRouter()

    @router.websocket("/v1/subscribe")
    async def websocket_handler(ws: WebSocket) -> None:
        """Handle a WebSocket subscription connection.

        Protocol:
          Client sends: {"type": "subscribe", "filter": {...}, "resume_from": {"seq": N}}
          Server pushes: matching events as JSON messages
        """
        await ws.accept()

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=subscriber_queue_maxsize
        )
        incoming: asyncio.Queue[str] = asyncio.Queue()
        disconnected = asyncio.Event()
        event_filter: dict[str, str] = {}
        subscriber_queues.add(queue)
        tracker.register(disconnected)
        push_task: asyncio.Task[None] | None = None
        # Sole receive consumer for the life of the socket. Replay sends are
        # not in receive_text, so without this a peer close during replay
        # never reaches finally and the queue stays in the fan-out set.
        reader_task = asyncio.create_task(
            _read_client_frames(ws, incoming, disconnected)
        )

        logger.info("Subscriber connected (%d total)", len(subscriber_queues))

        try:
            while not disconnected.is_set():
                raw = await _next_client_text(incoming, disconnected)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    if not await _send_json_or_dead(
                        ws, {"error": "Invalid JSON"}, disconnected
                    ):
                        break
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
                    resume_seq = (
                        resume_from.get("seq") if resume_from is not None else None
                    )

                    if resume_seq is not None:
                        replay_ok = True
                        async for row in _iter_replay_rows(store, resume_seq):
                            if not await _send_if_matches(
                                ws, row, event_filter, disconnected
                            ):
                                replay_ok = False
                                break
                        if not replay_ok:
                            break

                    realtime_filter = event_filter.get("role") in (
                        None,
                        "realtime",
                    )
                    if realtime_filter:
                        snapshot_ok = True
                        for rt_ev in store.get_realtime_snapshot(limit=1000):
                            if not await _send_if_matches(
                                ws, rt_ev, event_filter, disconnected
                            ):
                                snapshot_ok = False
                                break
                        if not snapshot_ok:
                            break

                    if push_task is not None:
                        await _cancel_task(push_task)
                    push_task = asyncio.create_task(
                        _push_loop(ws, queue, event_filter, disconnected)
                    )
                    if not await _send_json_or_dead(
                        ws,
                        {
                            "type": "subscribed",
                            "filter": event_filter,
                            "resumed_from": resume_from,
                        },
                        disconnected,
                    ):
                        break
                else:
                    if not await _send_json_or_dead(
                        ws,
                        {"error": f"Unknown message type: {msg_type}"},
                        disconnected,
                    ):
                        break

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("Unhandled subscriber error: %s", e, exc_info=True)
        finally:
            disconnected.set()
            if push_task is not None:
                push_task.cancel()
                try:
                    await asyncio.wait_for(push_task, timeout=1.0)
                except (asyncio.CancelledError, TimeoutError):
                    pass
                except Exception as e:
                    logger.error(
                        "Error awaiting cancelled push task: %s", e, exc_info=True
                    )
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
            subscriber_queues.discard(queue)
            tracker.discard(disconnected)
            logger.info(
                "Subscriber disconnected (%d remaining)", len(subscriber_queues)
            )

    return router


async def _iter_replay_rows(
    store: EventStore, resume_seq: object
) -> AsyncIterator[dict[str, Any]]:
    """Yield every row with seq > resume_seq. Pages; does not truncate the window."""
    last = resume_seq
    while True:
        rows = await store.query(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
            (last, _REPLAY_PAGE_SIZE),
            limit=_REPLAY_PAGE_SIZE,
        )
        if not rows:
            return
        for row in rows:
            yield row
            seq = row.get("seq")
            if seq is not None:
                last = seq
        if len(rows) < _REPLAY_PAGE_SIZE:
            return


async def _push_loop(
    ws: WebSocket,
    queue: asyncio.Queue[dict[str, Any]],
    event_filter: dict[str, str],
    disconnected: asyncio.Event,
) -> None:
    """Push matching events until the peer dies or the handler cancels us."""
    try:
        while not disconnected.is_set():
            event = await _next_queue_item(queue, disconnected)
            if event is None:
                return
            if not await _send_if_matches(ws, event, event_filter, disconnected):
                disconnected.set()
                return
    except asyncio.CancelledError:
        return


async def _next_queue_item(
    queue: asyncio.Queue[dict[str, Any]],
    disconnected: asyncio.Event,
) -> dict[str, Any] | None:
    get_task = asyncio.create_task(queue.get())
    stop_task = asyncio.create_task(disconnected.wait())
    try:
        done, _pending = await asyncio.wait(
            {get_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        get_task.cancel()
        stop_task.cancel()
        raise
    if disconnected.is_set() or get_task not in done:
        if get_task in done and not get_task.cancelled():
            get_task.result()
        else:
            get_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await get_task
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task
        return None
    stop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stop_task
    return get_task.result()
