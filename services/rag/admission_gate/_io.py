"""Async I/O: startup snapshot, WebSocket subscriber loop, first-burst emission."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import aiohttp
from transport_utils import DEFAULT_STARGATE_URL, make_async_client

from services.rag.events.admission import (
    rag_admission_first_burst_observed,
    rag_admission_io_failed,
)

from ._constants import (
    _EVENT_QUERY_SOCK,
    _RECONNECT_DELAY_S,
    _SNAPSHOT_TIMEOUT_S,
    _SUBSCRIBE_PATH,
)

if TYPE_CHECKING:
    from .gate import AdmissionGate

logger = logging.getLogger(__name__)


async def _snapshot(gate: AdmissionGate) -> None:
    """Fetch admission state from Stargate and pre-seed per-model events.

    Best-effort: any HTTP error leaves the gate in its default OPEN state.
    """
    async with make_async_client(
        DEFAULT_STARGATE_URL, timeout=_SNAPSHOT_TIMEOUT_S
    ) as client:
        for key in list(gate._tracked):
            url = "/api/v1/admission/state"
            try:
                resp = await client.get(url, params={"model_id": key})
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(
                    "AdmissionGate startup snapshot failed for %s "
                    "(defaulting OPEN; per-request timeout backstops correctness): %s",
                    key,
                    exc,
                )
                if gate._event_bus is not None:
                    await gate._event_bus.publish_nowait(
                        rag_admission_io_failed(
                            operation="snapshot",
                            model_id=key,
                            error=str(exc),
                        )
                    )
                continue

            should_close = bool(data.get("loading")) or bool(data.get("paused"))
            if should_close:
                ev = gate._tracked[key]
                if ev.is_set():
                    ev.clear()
                    reasons = gate._closed_reasons.setdefault(key, set())
                    if data.get("loading"):
                        reasons.add("model.loading")
                    if data.get("paused"):
                        reasons.add("capacity.admission")
                    logger.info(
                        "AdmissionGate: %s CLOSED from startup snapshot "
                        "(loading=%s, paused=%s, paused_reason=%s)",
                        key,
                        data.get("loading"),
                        data.get("paused"),
                        data.get("paused_reason"),
                    )
            else:
                logger.info(
                    "AdmissionGate: %s OPEN from startup snapshot "
                    "(loading=%s, paused=%s, queue_depth=%s)",
                    key,
                    data.get("loading"),
                    data.get("paused"),
                    data.get("queue_depth"),
                )


async def _emit_first_burst_observed(
    gate: AdmissionGate, key: str, workers_in_flight: int
) -> None:
    """Fetch Stargate queue depth and emit rag.admission.first.burst.observed.

    Best-effort: any HTTP error results in stargate_queue_depth=None in the
    payload. The event is still emitted so workers_in_flight is captured.
    """
    queue_depth: int | None = None
    try:
        async with make_async_client(
            DEFAULT_STARGATE_URL, timeout=_SNAPSHOT_TIMEOUT_S
        ) as client:
            resp = await client.get(
                "/api/v1/admission/state",
                params={"model_id": key},
            )
            resp.raise_for_status()
            raw = resp.json().get("queue_depth")
            if isinstance(raw, int):
                queue_depth = raw
    except Exception as exc:
        logger.warning(
            "AdmissionGate first-burst: queue_depth fetch failed for %s "
            "(emitting with stargate_queue_depth=None): %s",
            key,
            exc,
        )
        if gate._event_bus is not None:
            await gate._event_bus.publish_nowait(
                rag_admission_io_failed(
                    operation="burst_fetch",
                    model_id=key,
                    error=str(exc),
                )
            )
    if gate._event_bus is None:
        return
    try:
        await gate._event_bus.publish_nowait(
            rag_admission_first_burst_observed(
                model_id=key,
                workers_in_flight=workers_in_flight,
                stargate_queue_depth=queue_depth,
            )
        )
    except Exception as exc:
        logger.warning(
            "AdmissionGate first-burst: event emission failed for %s: %s",
            key,
            exc,
        )


async def _subscribe_loop(gate: AdmissionGate) -> None:
    """Subscribe to all events; filter in _apply_signal. Reconnect with backoff."""
    # No subscription filter — Event Service only supports single trailing-wildcard
    # per key; pipe-OR is not valid syntax. _apply_signal checks signal names
    # explicitly for capacity.admission.* and model.*. Event volume over UDS is
    # negligible so receiving all events is fine.
    while True:
        # Connector MUST be instantiated inside the loop. ClientSession
        # defaults connector_owner=True, so the connector is closed when
        # the session's async-with exits. Reusing a closed connector on the
        # next iteration raises; a fresh connector per iteration avoids it.
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
                            logger.info(
                                "AdmissionGate subscribed (resumed_from=%s, models=%s)",
                                event.get("resumed_from"),
                                sorted(gate._tracked.keys()),
                            )
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
                "AdmissionGate subscription error, reconnecting in %.0fs: %s",
                _RECONNECT_DELAY_S,
                exc,
            )
            await asyncio.sleep(_RECONNECT_DELAY_S)
