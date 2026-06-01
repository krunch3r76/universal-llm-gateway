"""Async I/O: admitted-set snapshot/refresh, WS subscriber, backstop loop."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp
from transport_utils import DEFAULT_CORTEX_URL, make_async_client

from ._constants import (
    _BACKSTOP_INTERVAL_S,
    _EVENT_QUERY_SOCK,
    _RECONNECT_DELAY_S,
    _REFRESH_DEBOUNCE_S,
    _SNAPSHOT_TIMEOUT_S,
    _SOURCE_PATHS_ENDPOINT,
    _SUBSCRIBE_PATH,
    _UNREADY_RETRY_S,
)

if TYPE_CHECKING:
    from .gate import EntityAdmissionGate

logger = logging.getLogger(__name__)


async def _refresh(gate: EntityAdmissionGate) -> None:
    """Full re-fetch of the admitted-path set from cortex-api.

    Authoritative full replace (not incremental) — avoids supersede-drops-
    source_uri bugs. Fail-safe: on any HTTP/parse error the prior set and
    _ready flag are left UNCHANGED (never cleared to empty on a transient
    blip). Serialized by gate._refresh_lock so the backstop and the dirty
    worker cannot interleave a partial update.
    """
    async with gate._refresh_lock:
        try:
            async with make_async_client(
                DEFAULT_CORTEX_URL, timeout=_SNAPSHOT_TIMEOUT_S
            ) as client:
                resp = await client.get(_SOURCE_PATHS_ENDPOINT)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            logger.warning(
                "EntityAdmissionGate refresh failed (keeping prior set of %d; "
                "gate holds fail-closed, reconcile loop retries): %s",
                len(gate._admitted),
                exc,
            )
            return

        raw_paths = data.get("paths") if isinstance(data, dict) else None
        if not isinstance(raw_paths, list):
            logger.warning(
                "EntityAdmissionGate refresh got malformed payload "
                "(missing 'paths' list); keeping prior set of %d",
                len(gate._admitted),
            )
            return

        new_set = {p for p in raw_paths if isinstance(p, str) and p}
        gate._admitted = new_set
        gate._ready = True
        logger.info(
            "EntityAdmissionGate refreshed: admitted=%d (unresolved=%s)",
            len(new_set),
            data.get("unresolved"),
        )


async def _backstop_loop(gate: EntityAdmissionGate) -> None:
    """Periodic full refresh — self-heals a missed source.changed event.

    Uses a short retry interval until the first successful load, then the
    steady backstop interval.
    """
    while True:
        interval = _BACKSTOP_INTERVAL_S if gate._ready else _UNREADY_RETRY_S
        await asyncio.sleep(interval)
        await _refresh(gate)


async def _dirty_refresh_worker(gate: EntityAdmissionGate) -> None:
    """Event-driven refresh: wait for dirty, debounce a burst, full re-fetch."""
    while True:
        await gate._dirty.wait()
        await asyncio.sleep(_REFRESH_DEBOUNCE_S)
        gate._dirty.clear()
        await _refresh(gate)


async def _subscribe_loop(gate: EntityAdmissionGate) -> None:
    """Subscribe to the Event Service WS; filter in _apply_signal. Reconnect.

    No subscription filter — the Event Service supports only a single
    trailing-wildcard key, so _apply_signal checks the signal name explicitly.
    A fresh UnixConnector per iteration avoids reusing a closed connector
    (ClientSession defaults connector_owner=True).
    """
    while True:
        connector = aiohttp.UnixConnector(path=_EVENT_QUERY_SOCK)
        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.ws_connect(_SUBSCRIBE_PATH) as ws:
                    msg: dict[str, object] = {"type": "subscribe"}
                    if gate._last_seq is not None:
                        msg["resume_from"] = {"seq": gate._last_seq}
                    await ws.send_json(msg)

                    async for raw in ws:
                        if raw.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            event = json.loads(raw.data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(event, dict):
                            continue
                        if event.get("type") == "subscribed":
                            logger.info("EntityAdmissionGate subscribed to events")
                            continue
                        seq = event.get("seq")
                        if isinstance(seq, int):
                            gate._last_seq = seq
                        signal = str(event.get("signal", ""))
                        payload_raw = event.get("payload")
                        payload = payload_raw if isinstance(payload_raw, dict) else {}
                        gate._apply_signal(signal, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "EntityAdmissionGate subscription error, reconnecting in %.0fs: %s",
                _RECONNECT_DELAY_S,
                exc,
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)
