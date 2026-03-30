"""Event bridge — forward scope=global events to an upstream Event Service.

Subscribes to the local Event Service via WebSocket, forwards matching
events to the upstream ingest socket via NDJSON/UDS.

Silent on connection failure — reconnects automatically.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_S = 2.0


class EventBridge:
    """Forward scope=global events from local to upstream Event Service."""

    def __init__(
        self,
        *,
        local_query_sock: str,
        upstream_ingest_sock: str,
        origin_node: str,
        scope_filter: str = "global",
    ) -> None:
        self._local_query_sock = local_query_sock
        self._upstream_ingest_sock = upstream_ingest_sock
        self._origin_node = origin_node
        self._scope_filter = scope_filter
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._forwarded = 0
        self._dropped = 0

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._bridge_loop())
        logger.info(
            "EventBridge started: %s → %s (origin=%s, scope=%s)",
            self._local_query_sock,
            self._upstream_ingest_sock,
            self._origin_node,
            self._scope_filter,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "EventBridge stopped (forwarded=%d, dropped=%d)",
            self._forwarded,
            self._dropped,
        )

    async def _bridge_loop(self) -> None:
        """Subscribe locally via WebSocket, forward upstream. Reconnect on failure."""
        while self._running:
            try:
                await self._run_subscription()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("EventBridge subscription failed: %s", e)
                if self._running:
                    await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _run_subscription(self) -> None:
        """Single subscription session — connect, filter, forward."""
        import websockets.client

        ws_uri = f"ws+unix://{self._local_query_sock}//v1/subscribe"
        async with websockets.client.connect(ws_uri, max_size=None) as ws:
            subscribe_msg = json.dumps(
                {
                    "type": "subscribe",
                    "filter": {"scope": self._scope_filter},
                }
            )
            await ws.send(subscribe_msg)

            async for raw in ws:
                if not self._running:
                    break
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                payload = event.get("payload") or {}
                payload["origin_node"] = self._origin_node
                event["payload"] = payload
                await self._forward(event)

    async def _forward(self, event: dict[str, Any]) -> None:
        """Send one event to upstream ingest socket (fire-and-forget)."""
        line = json.dumps(event, default=str) + "\n"
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._upstream_ingest_sock),
                timeout=2.0,
            )
            writer.write(line.encode())
            await asyncio.wait_for(writer.drain(), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            self._forwarded += 1
        except Exception:
            self._dropped += 1
            logger.debug("EventBridge forward failed", exc_info=True)
