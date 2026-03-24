"""Event-driven model state tracker.

Subscribes to model lifecycle events from Event Service WebSocket.
Maintains a gate (asyncio.Event) that workers await before sending
requests to Stargate.

∀ guarded attempt: wait_for_model() → True (proceed) ∨ False (timeout).
Prevents startup-surge failures by holding workers until the model is
confirmed loaded.

Two subscription paths run concurrently:
- ``model.*`` — lifecycle events from the local (edge-localhost) gateway
- ``federation.model.lifecycle`` — lifecycle events from remote gateways
  (relay-jupiter etc.) forwarded by the master Stargate

At start() time the probe queries the Event Service for the most recent
load/unload event from either path, covering the case where the model was
already loaded before RAG started.

``ExtractionModelTracker`` is a backward-compatible alias for
``ModelStateTracker``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_EVENT_QUERY_SOCK = "/tmp/universal-protocol/events-query.sock"
_RECONNECT_DELAY_S = 5.0
_DEFAULT_WAIT_TIMEOUT_S = 600.0
_PROBE_TIMEOUT_S = 5.0


class ModelState(Enum):
    UNKNOWN = "unknown"
    LOADED = "loaded"
    UNLOADED = "unloaded"


class ModelStateTracker:
    """Track model loaded state via Event Service WebSocket.

    Workers call wait_for_model() before sending requests. Gate opens on
    model.loaded, closes on model.unloaded. UNKNOWN state allows one
    request through (to trigger the load via Stargate routing).
    """

    def __init__(
        self,
        model_id: str,
        pipeline_id: str = "",
        *,
        event_socket: str = _EVENT_QUERY_SOCK,
        wait_timeout_s: float = _DEFAULT_WAIT_TIMEOUT_S,
    ) -> None:
        self._model_id = model_id
        self._pipeline_id = pipeline_id
        self._event_socket = event_socket
        self._wait_timeout_s = wait_timeout_s
        self._state = ModelState.UNKNOWN
        self._model_ready = asyncio.Event()
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_fed_task: asyncio.Task[None] | None = None
        self._ws_sys_task: asyncio.Task[None] | None = None
        self._first_request_sent = False

    @property
    def state(self) -> ModelState:
        return self._state

    @property
    def is_loaded(self) -> bool:
        return self._state == ModelState.LOADED

    async def start(self) -> None:
        """Probe initial model state then start WebSocket subscriptions.

        Three concurrent subscriptions:
        - ``model.*`` — local gateway lifecycle
        - ``federation.model.lifecycle`` — remote gateway lifecycle
        - ``system.*`` — reset to UNKNOWN on Stargate restart (system.started)

        The probe runs first so that models already loaded before RAG started
        (no event will re-fire) start in LOADED state, unless Stargate has
        restarted more recently than the last model.loaded event (stale probe).
        """
        await self._probe_initial_state()
        self._ws_task = asyncio.create_task(
            self._subscribe_loop({"signal": "model.*"}),
            name="extraction-model-tracker-local",
        )
        self._ws_fed_task = asyncio.create_task(
            self._subscribe_loop({"signal": "federation.model.lifecycle"}),
            name="extraction-model-tracker-federation",
        )
        self._ws_sys_task = asyncio.create_task(
            self._subscribe_loop({"signal": "system.*"}),
            name="extraction-model-tracker-system",
        )

    async def _probe_initial_state(self) -> None:
        """Set initial state from the most recent model lifecycle event in Event Service.

        Covers both local gateway events (model.loaded / model.unloaded) and
        remote gateway events (federation.model.lifecycle with msg_type
        telemetry.model.loaded / telemetry.model.unloaded). Takes whichever is
        most recent by seq.

        Staleness guard: if the most recent system.started event has a higher
        seq than the most recent lifecycle event, Stargate restarted after the
        model was last seen loaded. Model state is unknown post-restart, so we
        stay UNKNOWN (first request will re-trigger the load).

        Falls back to UNKNOWN on any error (first request will trigger the load).
        """
        lifecycle_sql = (
            "SELECT seq, signal, json_extract(payload, '$.msg_type') AS msg_type "
            "FROM events "
            "WHERE ("
            "  (signal IN ('model.loaded', 'model.unloaded')"
            "   AND json_extract(payload, '$.model_id') LIKE ?)"
            "  OR"
            "  (signal = 'federation.model.lifecycle'"
            "   AND json_extract(payload, '$.model_id') LIKE ?"
            "   AND json_extract(payload, '$.msg_type')"
            "       IN ('telemetry.model.loaded', 'telemetry.model.unloaded'))"
            ") "
            "ORDER BY seq DESC LIMIT 1"
        )
        system_sql = "SELECT seq FROM events WHERE signal='system.started' ORDER BY seq DESC LIMIT 1"
        params = [f"{self._model_id}%", f"{self._model_id}%"]
        try:
            connector = aiohttp.UnixConnector(path=self._event_socket)
            async with aiohttp.ClientSession(connector=connector) as session:
                timeout = aiohttp.ClientTimeout(total=_PROBE_TIMEOUT_S)

                async with session.post(
                    "http://localhost/v1/query",
                    json={"type": "sql", "sql": lifecycle_sql, "params": params},
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        return
                    body = await resp.json()
                    lifecycle_rows = body.get("rows", [])

                async with session.post(
                    "http://localhost/v1/query",
                    json={"type": "sql", "sql": system_sql, "params": []},
                    timeout=timeout,
                ) as resp:
                    if resp.status != 200:
                        return
                    body = await resp.json()
                    system_rows = body.get("rows", [])

            if not lifecycle_rows:
                logger.debug(
                    "No prior model lifecycle events for '%s'"
                    " — starting in UNKNOWN state",
                    self._model_id,
                )
                return

            lifecycle_seq: int = lifecycle_rows[0].get("seq", 0)
            system_seq: int = system_rows[0].get("seq", 0) if system_rows else 0

            if system_seq > lifecycle_seq:
                logger.info(
                    "Stargate restarted (system.started seq=%d) after last"
                    " lifecycle event for '%s' (seq=%d)"
                    " — model state unknown post-restart, starting UNKNOWN",
                    system_seq,
                    self._model_id,
                    lifecycle_seq,
                )
                return

            row = lifecycle_rows[0]
            signal = row.get("signal", "")
            msg_type = row.get("msg_type") or ""
            is_loaded = signal == "model.loaded" or msg_type == "telemetry.model.loaded"
            if is_loaded:
                self._state = ModelState.LOADED
                self._model_ready.set()
                logger.info(
                    "Most recent lifecycle event for '%s' indicates LOADED"
                    " (signal=%s msg_type=%s seq=%d) — initialising tracker as LOADED",
                    self._model_id,
                    signal,
                    msg_type or "n/a",
                    lifecycle_seq,
                )
            else:
                logger.info(
                    "Most recent lifecycle event for '%s' indicates UNLOADED"
                    " (signal=%s msg_type=%s)"
                    " — starting in UNKNOWN state, first request will trigger load",
                    self._model_id,
                    signal,
                    msg_type or "n/a",
                )
        except Exception as exc:
            logger.debug(
                "Extraction model state probe failed (starting UNKNOWN): %s", exc
            )

    async def stop(self) -> None:
        """Cancel all subscription tasks."""
        for attr in ("_ws_task", "_ws_fed_task", "_ws_sys_task"):
            task: asyncio.Task[None] | None = getattr(self, attr)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, attr, None)

    async def wait_until_loaded(self, timeout_s: float) -> bool:
        """Block until the extraction model is confirmed LOADED.

        Used for startup gating — does NOT consume the first-request token.
        Returns True if the model reaches LOADED within timeout_s, False
        otherwise. Callers should proceed with on-demand load on False.
        """
        if self._state == ModelState.LOADED:
            return True
        logger.info(
            "Model '%s' state=%s at startup — waiting up to %.0fs"
            " for model.loaded before releasing watcher",
            self._model_id,
            self._state.value,
            timeout_s,
        )
        try:
            await asyncio.wait_for(
                self._model_ready.wait(), timeout=timeout_s
            )
            return True
        except TimeoutError:
            logger.warning(
                "Model '%s' not confirmed loaded after %.0fs —"
                " proceeding; on-demand load will activate on first request",
                self._model_id,
                timeout_s,
            )
            return False

    async def wait_for_model(self, timeout_s: float | None = None) -> bool:
        """Wait until extraction model is loaded.

        Returns True if model is ready, False on timeout.
        In UNKNOWN state, returns True once (to trigger the first load)
        then blocks subsequent callers until model.loaded fires.
        """
        if self._state == ModelState.LOADED:
            return True

        if self._state == ModelState.UNKNOWN and not self._first_request_sent:
            self._first_request_sent = True
            logger.info(
                "Model '%s' state unknown — allowing first request to trigger load",
                self._model_id,
            )
            return True

        effective_timeout = timeout_s if timeout_s is not None else self._wait_timeout_s
        logger.info(
            "Model '%s' not loaded (state=%s) — waiting up to %.0fs",
            self._model_id,
            self._state.value,
            effective_timeout,
        )
        try:
            await asyncio.wait_for(
                self._model_ready.wait(), timeout=effective_timeout
            )
            return True
        except TimeoutError:
            logger.warning(
                "Model '%s' not loaded after %.0fs wait",
                self._model_id,
                effective_timeout,
            )
            return False

    def _matches_model(self, event_model_id: str) -> bool:
        """Check if event model_id matches the configured model.

        Handles context-suffixed variants: if model_id is
        'qwen3-14b-q4-k-m-40960', matches 'qwen3-14b-q4-k-m-40960-8192'.
        """
        if not event_model_id or not self._model_id:
            return False
        return (
            event_model_id == self._model_id
            or event_model_id.startswith(self._model_id + "-")
        )

    def _handle_event(self, event: dict[str, Any]) -> None:
        """Process a model lifecycle event (local or federation)."""
        signal = event.get("signal", "")
        payload = event.get("payload", {})
        model_id = payload.get("model_id", "")

        if not self._matches_model(model_id):
            return

        if signal == "model.loaded":
            prev = self._state
            self._state = ModelState.LOADED
            self._model_ready.set()
            logger.info("Model '%s' loaded (was %s)", model_id, prev.value)
        elif signal == "model.unloaded":
            self._state = ModelState.UNLOADED
            self._model_ready.clear()
            logger.info("Model '%s' unloaded", model_id)
        elif signal == "federation.model.lifecycle":
            msg_type = payload.get("msg_type", "")
            gateway_id = payload.get("gateway_id", "?")
            if msg_type == "telemetry.model.loaded":
                prev = self._state
                self._state = ModelState.LOADED
                self._model_ready.set()
                logger.info(
                    "Model '%s' loaded on remote gateway %s (was %s)",
                    model_id,
                    gateway_id,
                    prev.value,
                )
            elif msg_type == "telemetry.model.unloaded":
                self._state = ModelState.UNLOADED
                self._model_ready.clear()
                logger.info(
                    "Model '%s' unloaded from remote gateway %s",
                    model_id,
                    gateway_id,
                )

    def _handle_system_event(self, event: dict[str, Any]) -> None:
        """Reset tracker state when Stargate restarts.

        system.started means the Stargate process just started — all gateway
        connections are being re-established and model state is unknown.
        Resetting to UNKNOWN ensures workers wait for a fresh model.loaded
        or federation.model.lifecycle event rather than acting on stale state.
        """
        if event.get("signal") != "system.started":
            return
        if self._state != ModelState.UNKNOWN:
            logger.info(
                "Stargate restarted (system.started) — resetting model tracker"
                " for '%s' to UNKNOWN (was %s)",
                self._model_id,
                self._state.value,
            )
            self._state = ModelState.UNKNOWN
            self._model_ready.clear()
            self._first_request_sent = False

    async def _subscribe_loop(self, event_filter: dict[str, str]) -> None:
        """Connect to Event Service WebSocket with reconnection."""
        while True:
            try:
                await self._subscribe_once(event_filter)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Event subscription disconnected (filter=%s): %s;"
                    " reconnecting in %.0fs",
                    event_filter,
                    exc,
                    _RECONNECT_DELAY_S,
                )
            await asyncio.sleep(_RECONNECT_DELAY_S)

    async def _subscribe_once(self, event_filter: dict[str, str]) -> None:
        """Single WebSocket session to Event Service."""
        is_system_filter = event_filter.get("signal", "").startswith("system")
        connector = aiohttp.UnixConnector(path=self._event_socket)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.ws_connect("http://localhost/v1/subscribe") as ws:
                await ws.send_json({"type": "subscribe", "filter": event_filter})

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if event.get("type") == "subscribed":
                            logger.info(
                                "Subscribed to model lifecycle events (filter=%s)",
                                event_filter,
                            )
                            continue
                        if is_system_filter:
                            self._handle_system_event(event)
                        else:
                            self._handle_event(event)
                    elif msg.type in (
                        aiohttp.WSMsgType.ERROR,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        break


# Backward-compatible alias — callers using ExtractionModelTracker continue to work.
ExtractionModelTracker = ModelStateTracker
