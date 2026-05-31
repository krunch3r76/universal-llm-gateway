"""Aggregate model availability for RAG embedding and extraction paths.

Subscribes to the Event Service WebSocket for `model.available` /
`model.unavailable` signals — the authoritative source per event contracts.
HTTP seed provides initial snapshot at startup; real-time updates arrive
via WebSocket push with resume-from-seq replay on reconnect.

∀ model load/unload: Stargate is sole authority. RAG never observes load
state — only catalog presence (`model.available` = routable in aggregate).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum

import aiohttp
import httpx

logger = logging.getLogger(__name__)

_STARGATE_BASE = "http://localhost:9999"
_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"  # host ignored with UDS connector
_client = httpx.AsyncClient(timeout=30.0)
_RECONNECT_DELAY_S = 5.0

_tracker: ModelAvailabilityTracker | None = None


class AvailabilityReason(Enum):
    """Why a model is available or unavailable — from RAG's perspective.

    RAG does not observe model load state (Stargate orchestrates loading on
    demand). The only structural question is catalog presence.
    """

    ROUTABLE = "routable"
    NOT_IN_CATALOG = "not_in_catalog"
    PROBE_FAILED = "probe_failed"

    @property
    def is_structural(self) -> bool:
        return self is AvailabilityReason.NOT_IN_CATALOG

    @property
    def is_transient(self) -> bool:
        return self is AvailabilityReason.PROBE_FAILED


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    """Outcome of a model availability check with classified reason.

    Invariants: available=True requires reason=ROUTABLE;
    available=False requires reason != ROUTABLE.
    """

    available: bool
    reason: AvailabilityReason
    detail: str = ""

    def __post_init__(self) -> None:
        if self.available and self.reason is not AvailabilityReason.ROUTABLE:
            raise ValueError(
                f"available=True requires reason=ROUTABLE, got {self.reason}"
            )
        if not self.available and self.reason is AvailabilityReason.ROUTABLE:
            raise ValueError("available=False cannot use reason=ROUTABLE")


class ModelAvailabilityStartError(RuntimeError):
    """Transient Stargate watch-registration failure during RAG dependency activation."""


class ModelAvailabilityTracker:
    """Maintain aggregate routability state for RAG dependencies.

    State is driven by the Event Service WebSocket subscription
    (`model.available` / `model.unavailable`). HTTP seed provides initial
    snapshot. The subscribe loop reconnects automatically and resumes from
    the last seen sequence number.
    """

    def __init__(self) -> None:
        self._model_ids: set[str] = set()
        self._available: dict[str, bool] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._started = False
        self._subscribe_task: asyncio.Task[None] | None = None
        self._last_seq: int | None = None

    def _ensure_slot(self, model_id: str) -> asyncio.Event:
        if model_id not in self._events:
            self._available[model_id] = False
            self._events[model_id] = asyncio.Event()
        return self._events[model_id]

    def _set_available(self, model_id: str, available: bool) -> None:
        self._ensure_slot(model_id)
        self._available[model_id] = available
        ev = self._events[model_id]
        if available:
            ev.set()
        else:
            ev.clear()

    async def configure(self, model_ids: list[str]) -> None:
        """Register tracked model IDs. Does not start the Event Service subscriber.

        Call refresh_snapshot() to seed initial state, then start_subscription()
        to begin WebSocket delivery. Separating these three stages prevents a
        race where WS events arrive before the snapshot seeds state.
        """
        if self._started:
            await self.stop()
        self._model_ids = {mid for mid in model_ids if mid}
        for mid in self._model_ids:
            self._ensure_slot(mid)
        self._started = True

    def start_subscription(self) -> None:
        """Start the background Event Service WebSocket subscriber task.

        Must be called after configure() and refresh_snapshot(). Idempotent:
        no-op if the subscription task is already running.

        Lifecycle order: configure() → refresh_snapshot() → start_subscription()
        """
        if not self._started:
            raise RuntimeError("configure() must be called before start_subscription()")
        if self._subscribe_task is None or self._subscribe_task.done():
            self._subscribe_task = asyncio.create_task(
                self._subscribe_loop(), name="model-availability-subscriber"
            )

    def _parse_payload(self, raw: object) -> dict[str, object]:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    async def _subscribe_loop(self) -> None:
        """Subscribe to Event Service WebSocket; reconnect with backoff on failure.

        Filters model.available and model.unavailable. On reconnect, resumes
        from the last seen seq to catch events missed during downtime.
        """
        while True:
            # Connector MUST be instantiated inside the loop. ClientSession
            # defaults connector_owner=True, so the connector is closed when
            # the session's async-with exits. Reusing a closed connector on the
            # next iteration raises; a fresh connector per iteration avoids it.
            connector = aiohttp.UnixConnector(path=_EVENT_QUERY_SOCK)
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
                                    "ModelAvailabilityTracker subscribed to Event Service"
                                    " (resumed_from=%s)",
                                    event.get("resumed_from"),
                                )
                                continue

                            seq = event.get("seq")
                            if isinstance(seq, int):
                                self._last_seq = seq

                            signal = str(event.get("signal", ""))
                            payload = self._parse_payload(event.get("payload"))
                            mid = payload.get("model_id")
                            if not isinstance(mid, str) or mid not in self._model_ids:
                                continue

                            if signal == "model.available":
                                self._set_available(mid, True)
                                logger.info(
                                    "Model %s became routable (Event Service)", mid
                                )
                            elif signal == "model.unavailable":
                                self._set_available(mid, False)
                                logger.info(
                                    "Model %s became unroutable (Event Service)", mid
                                )

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "Event Service subscription error, reconnecting in %.0fs: %s",
                    _RECONNECT_DELAY_S,
                    exc,
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)

    async def refresh_snapshot(self) -> dict[str, bool]:
        """Fetch the current aggregate availability snapshot from Stargate.

        Used at startup to seed state before the WebSocket stream catches up.

        Raises:
            ModelAvailabilityStartError: Stargate is unreachable or rejects the watch call.
            RuntimeError: Tracker was not configured before refresh.
        """
        if not self._started:
            raise RuntimeError(
                "ModelAvailabilityTracker must be configured before refresh"
            )

        url = f"{_STARGATE_BASE}/api/v1/model-availability/watch"
        try:
            resp = await _client.post(
                url,
                json={"model_ids": sorted(self._model_ids)},
            )
            resp.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise ModelAvailabilityStartError(
                f"Failed to register model-availability watch: {exc}"
            ) from exc

        snap = resp.json()
        if isinstance(snap, dict):
            for mid, ok in snap.items():
                if mid in self._model_ids and isinstance(ok, bool):
                    self._set_available(mid, ok)

        logger.info(
            "ModelAvailabilityTracker snapshot refreshed for %s (snapshot=%s)",
            sorted(self._model_ids),
            snap,
        )
        return {mid: self.is_available(mid) for mid in sorted(self._model_ids)}

    async def stop(self) -> None:
        """Cancel the subscribe task and reset state."""
        self._started = False
        if self._subscribe_task is not None:
            self._subscribe_task.cancel()
            try:
                await self._subscribe_task
            except asyncio.CancelledError:
                pass
            self._subscribe_task = None

    def is_available(self, model_id: str) -> bool:
        """Return last known aggregate availability for this model ID."""
        return bool(self._available.get(model_id, False))

    async def _probe_catalog_presence(self, model_id: str) -> AvailabilityResult:
        """One-shot catalog check when wait_until_available times out.

        Classifies structural (NOT_IN_CATALOG) vs transient (PROBE_FAILED).
        Uses GET /v1/models/{model_id} — checks only `available`, never load state.
        """
        url = f"{_STARGATE_BASE}/v1/models/{model_id}"
        try:
            resp = await _client.get(url, timeout=5.0)
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.warning("Catalog probe failed for %s: %s", model_id, exc)
            return AvailabilityResult(
                available=False,
                reason=AvailabilityReason.PROBE_FAILED,
                detail=f"probe failed: {exc}",
            )

        if resp.status_code == 404:
            return AvailabilityResult(
                available=False,
                reason=AvailabilityReason.NOT_IN_CATALOG,
                detail=f"model {model_id} not found in catalog (404)",
            )

        if resp.status_code != 200:
            return AvailabilityResult(
                available=False,
                reason=AvailabilityReason.PROBE_FAILED,
                detail=f"unexpected status {resp.status_code}",
            )

        data = resp.json()
        if data.get("available", False):
            return AvailabilityResult(
                available=True,
                reason=AvailabilityReason.ROUTABLE,
                detail=f"model {model_id} exists in catalog",
            )

        return AvailabilityResult(
            available=False,
            reason=AvailabilityReason.NOT_IN_CATALOG,
            detail=f"model {model_id} not available in catalog",
        )

    async def wait_until_available(
        self, model_id: str, timeout_s: float
    ) -> AvailabilityResult:
        """Wait until model_id is routable (set by Event Service push) or timeout.

        On timeout, runs one catalog probe to classify structural vs transient.
        Normal flow: Event Service pushes model.available → asyncio.Event fires.
        Raises KeyError if model_id was not registered via configure().
        """
        if model_id not in self._model_ids:
            raise KeyError(
                f"Model {model_id!r} is not configured for tracking; "
                f"call configure() with this model_id first"
            )
        if self.is_available(model_id):
            return AvailabilityResult(
                available=True, reason=AvailabilityReason.ROUTABLE
            )
        probe = await self._probe_catalog_presence(model_id)
        if probe.available:
            self._set_available(model_id, True)
            logger.info(
                "Model %s catalog probe short-circuited unavailable tracker state",
                model_id,
            )
            return probe
        ev = self._ensure_slot(model_id)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
        except TimeoutError:
            result = await self._probe_catalog_presence(model_id)
            if result.reason.is_structural:
                logger.error(
                    "Model %s not in catalog (structural): %s", model_id, result.detail
                )
            else:
                logger.warning(
                    "Model %s timed out (transient): %s", model_id, result.detail
                )
            return result
        if self.is_available(model_id):
            return AvailabilityResult(
                available=True, reason=AvailabilityReason.ROUTABLE
            )
        return await self._probe_catalog_presence(model_id)


def get_model_availability_tracker() -> ModelAvailabilityTracker | None:
    """Return the process singleton tracker if configured."""
    return _tracker


def set_model_availability_tracker(t: ModelAvailabilityTracker | None) -> None:
    """Set the process singleton (lifecycle owns creation)."""
    global _tracker
    _tracker = t


async def close_model_availability_client() -> None:
    """Close the shared HTTP client on RAG shutdown."""
    await _client.aclose()
