"""Lifecycle coordinator for batch contextualization workers.

Subscribes to Stargate-emitted model lifecycle signals on the Event Service
WebSocket and maintains a per-model `asyncio.Event` workers can await before
acquiring the global contextualization gate. Purpose: keep N concurrent
indexers from stampeding the queue while their target model is mid-load or
just evicted by a foreground request.

Per the stargate-model-lifecycle invariant this is **coordination only** —
the per-chunk client timeout remains the correctness backstop. Falling back
to an empty signal stream (Event Service unreachable, signal lost) degrades
to today's behavior, never breaks correctness.

Subscribed signals (all `model.*`, `role=coordination`):
  - model.loading.started — cold-load window opened → mark unavailable
                            (workers pause to avoid stampeding mid-load)
  - model.loaded           — cold-load completed → mark available
  - model.load.failed      — cold-load attempt failed → restore available so
                             the next worker's request triggers a retry
                             (Stargate re-loads on demand)

Why `model.unloaded` is intentionally ignored
---------------------------------------------
A bare unload (eviction) without a subsequent `model.loading.started` does
not mean "workers should pause" — the very next contextualize request will
trigger Stargate to reload the model on demand. Pausing here would *force*
every batch to wait the full coordinator timeout for a `model.loaded` signal
that may never come if the upstream emission chain is incomplete (see
`todo:stargate-model-load-event-emission-gap`).

The coordinator's contract: **clear only when there is observable evidence
of a cold-load in progress**. With that contract, a missing or partial
signal stream degrades to no-op (today's behavior) instead of a 60-second
penalty per batch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable

import aiohttp

logger = logging.getLogger(__name__)

_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"  # host ignored with UDS
_RECONNECT_DELAY_S = 5.0


class ContextualizeModelCoordinator:
    """Per-model load-state hint for batch contextualization workers.

    State per tracked model_id:
      - asyncio.Event SET   ⇒ believed available; workers may acquire gate.
      - asyncio.Event CLEAR ⇒ believed loading or just evicted; workers
                              should pause to avoid stampeding.

    Default is SET — coordination is a hint, not a correctness gate. A
    timeout in `wait_for_available` returns False and the worker proceeds
    anyway (the per-chunk client timeout backstops correctness).
    """

    def __init__(self, model_ids: Iterable[str]) -> None:
        self._model_ids: set[str] = {m for m in model_ids if m}
        self._available: dict[str, asyncio.Event] = {}
        for mid in self._model_ids:
            ev = asyncio.Event()
            ev.set()
            self._available[mid] = ev
        self._task: asyncio.Task[None] | None = None
        self._last_seq: int | None = None

    def start(self) -> None:
        """Spawn the background subscriber task. Idempotent."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._subscribe_loop(), name="contextualize-coordinator"
            )

    async def stop(self) -> None:
        """Cancel the subscriber task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def wait_for_available(self, model_id: str, timeout: float) -> bool:
        """Wait until model_id is believed available, capped by timeout.

        Returns True if the model is available (or untracked); False on
        timeout. Callers MUST proceed regardless — coordination is a hint,
        not a correctness gate.
        """
        ev = self._available.get(model_id)
        if ev is None:
            return True
        if ev.is_set():
            return True
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Contextualize coordinator timed out waiting for %s "
                "(proceeding; per-chunk timeout will backstop)",
                model_id,
            )
            return False
        return True

    def _set_available(self, model_id: str, available: bool, *, signal: str) -> None:
        ev = self._available.get(model_id)
        if ev is None:
            return
        if available and not ev.is_set():
            ev.set()
            logger.info(
                "Contextualize coordinator: %s available (via %s)", model_id, signal
            )
        elif not available and ev.is_set():
            ev.clear()
            logger.info(
                "Contextualize coordinator: %s unavailable (via %s)",
                model_id,
                signal,
            )

    def _apply_signal(self, signal: str, payload: dict[str, object]) -> None:
        mid = payload.get("model_id")
        if not isinstance(mid, str) or mid not in self._model_ids:
            return
        if signal == "model.loaded":
            self._set_available(mid, True, signal=signal)
        elif signal == "model.load.failed":
            # Restore optimism so the next worker's request can trigger a
            # reload. The caller's request itself will fail loudly — that
            # is the correctness signal.
            self._set_available(mid, True, signal=signal)
        elif signal == "model.loading.started":
            # Only clear when a cold-load is actively in progress. See the
            # module docstring for why `model.unloaded` is intentionally
            # ignored.
            self._set_available(mid, False, signal=signal)

    async def _subscribe_loop(self) -> None:
        """Subscribe to `model.*`; reconnect with backoff. Resume from last seq."""
        connector = aiohttp.UnixConnector(path=_EVENT_QUERY_SOCK)
        while True:
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(_SUBSCRIBE_PATH) as ws:
                        msg: dict[str, object] = {
                            "type": "subscribe",
                            "filter": {"signal": "model.*"},
                        }
                        if self._last_seq is not None:
                            msg["resume_from"] = {"seq": self._last_seq}
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
                                    "Contextualize coordinator subscribed "
                                    "(resumed_from=%s, models=%s)",
                                    event.get("resumed_from"),
                                    sorted(self._model_ids),
                                )
                                continue

                            seq = event.get("seq")
                            if isinstance(seq, int):
                                self._last_seq = seq

                            signal = str(event.get("signal", ""))
                            payload_raw = event.get("payload")
                            payload = (
                                payload_raw if isinstance(payload_raw, dict) else {}
                            )
                            self._apply_signal(signal, payload)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "Contextualize coordinator subscription error, "
                    "reconnecting in %.0fs: %s",
                    _RECONNECT_DELAY_S,
                    exc,
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)
