"""Observation-driven admission gate for the RAG extraction worker."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from typing import TYPE_CHECKING

import aiohttp

from services.rag.events.extraction_admission import (
    rag_extraction_admission_closed,
    rag_extraction_admission_opened,
)

if TYPE_CHECKING:
    from universal_event_bus import Event, EventBus

logger = logging.getLogger(__name__)

_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"
_RECONNECT_DELAY_S = 5.0

_TIMEOUT_BURST_THRESHOLD: int = 3
_FAILURE_RATIO_THRESHOLD: float = 0.3
_CAPACITY_FAILURE_TYPES: frozenset[str] = frozenset({"timeout", "inference_timeout"})
_REASON_TIMEOUT_BURST = "iteration-timeout-burst"
_REASON_STEP_FAILURE_RATIO = "step-failure-ratio"


class ExtractionAdmissionGate:
    def __init__(
        self,
        pipeline_id: str,
        *,
        event_bus: EventBus | None = None,
    ) -> None:
        if not pipeline_id:
            raise ValueError("ExtractionAdmissionGate requires a non-empty pipeline_id")
        self._pipeline_id = pipeline_id
        self._gate = asyncio.Event()
        self._gate.set()
        self._closed_reasons: set[str] = set()
        self._recent_failure_types: deque[str] = deque(maxlen=_TIMEOUT_BURST_THRESHOLD)
        self._observed_models: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._last_seq: int | None = None
        self._event_bus = event_bus
        self._closed_at_monotonic: float | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._subscribe_loop(),
                name=f"rag-extraction-admission-{self._pipeline_id}",
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait({self._task})
            self._task = None

    async def wait_for_admission(self, timeout: float) -> bool:
        """Return True when admission opens before timeout."""
        if self._gate.is_set():
            return True
        try:
            await asyncio.wait_for(self._gate.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "ExtractionAdmissionGate(%s) timed out waiting "
                "(active_reasons=%s; proceeding optimistically)",
                self._pipeline_id,
                sorted(self._closed_reasons),
            )
            return False
        return True

    def is_closed(self) -> bool:
        return not self._gate.is_set()

    def active_reasons(self) -> list[str]:
        return sorted(self._closed_reasons)

    def _close_with_reason(self, reason: str, *, signal: str) -> bool:
        self._closed_reasons.add(reason)
        if self._gate.is_set():
            self._gate.clear()
            self._closed_at_monotonic = time.monotonic()
            logger.info(
                "ExtractionAdmissionGate(%s) CLOSED (signal=%s, reason=%s, "
                "active_reasons=%s)",
                self._pipeline_id,
                signal,
                reason,
                sorted(self._closed_reasons),
            )
            self._publish(
                rag_extraction_admission_closed(
                    pipeline_id=self._pipeline_id,
                    reason=reason,
                    active_reasons=sorted(self._closed_reasons),
                    signal=signal,
                )
            )
            return True
        return False

    def _open_for_reason(self, reason: str, *, signal: str) -> bool:
        if reason not in self._closed_reasons:
            return False
        self._closed_reasons.discard(reason)
        if not self._closed_reasons and not self._gate.is_set():
            self._gate.set()
            closed_seconds = (
                time.monotonic() - self._closed_at_monotonic
                if self._closed_at_monotonic is not None
                else 0.0
            )
            self._closed_at_monotonic = None
            logger.info(
                "ExtractionAdmissionGate(%s) OPEN (signal=%s, "
                "cleared_reason=%s, closed_seconds=%.1f)",
                self._pipeline_id,
                signal,
                reason,
                closed_seconds,
            )
            self._publish(
                rag_extraction_admission_opened(
                    pipeline_id=self._pipeline_id,
                    cleared_reason=reason,
                    signal=signal,
                    closed_seconds=closed_seconds,
                )
            )
            return True
        return False

    def _publish(self, event: Event) -> None:
        if self._event_bus is None:
            return
        self._event_bus.publish_from_sync(event)

    def _handle_iteration_started(self, payload: dict[str, object]) -> None:
        if payload.get("pipeline_id") != self._pipeline_id:
            return
        model_id = payload.get("model_id")
        if isinstance(model_id, str) and model_id:
            self._observed_models.add(model_id)

    def _handle_iteration_failed(self, payload: dict[str, object]) -> None:
        if payload.get("pipeline_id") != self._pipeline_id:
            return
        failure_type = payload.get("failure_type")
        if not isinstance(failure_type, str):
            return
        if failure_type not in _CAPACITY_FAILURE_TYPES:
            self._recent_failure_types.clear()
            return
        self._recent_failure_types.append(failure_type)
        if len(self._recent_failure_types) >= _TIMEOUT_BURST_THRESHOLD:
            self._close_with_reason(
                _REASON_TIMEOUT_BURST,
                signal="pipeline.map.iteration.failed",
            )

    def _handle_step_completed(self, payload: dict[str, object]) -> None:
        if payload.get("pipeline_id") != self._pipeline_id:
            return
        try:
            failed = int(payload.get("failed_count", 0))
            total = int(payload.get("total_count", 0))
        except (TypeError, ValueError):
            return
        if total <= 0:
            return
        ratio = failed / total
        if failed == 0:
            self._open_for_reason(
                _REASON_TIMEOUT_BURST, signal="pipeline.map.completed"
            )
            self._open_for_reason(
                _REASON_STEP_FAILURE_RATIO, signal="pipeline.map.completed"
            )
            self._recent_failure_types.clear()
            return
        if ratio >= _FAILURE_RATIO_THRESHOLD:
            self._close_with_reason(
                _REASON_STEP_FAILURE_RATIO,
                signal="pipeline.map.completed",
            )

    def _handle_gateway_signal(self, signal: str, payload: dict[str, object]) -> None:
        gateway_id = payload.get("gateway_id")
        if not isinstance(gateway_id, str) or not gateway_id:
            return
        reason = f"gateway:{gateway_id}"
        if signal == "federation.gateway.degraded":
            self._close_with_reason(reason, signal=signal)
        elif signal == "federation.gateway.recovered":
            self._open_for_reason(reason, signal=signal)

    def _handle_model_signal(self, signal: str, payload: dict[str, object]) -> None:
        model_id = payload.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            return
        if model_id not in self._observed_models:
            return
        reason = f"model:{model_id}"
        if signal == "model.loading.started":
            self._close_with_reason(reason, signal=signal)
        elif signal in ("model.loaded", "model.load.failed"):
            # model.load.failed clears the gate to restore optimism — the next
            # request will re-trigger a load and surface the failure loudly.
            self._open_for_reason(reason, signal=signal)

    def _apply_signal(self, signal: str, payload: dict[str, object]) -> None:
        if signal == "pipeline.map.iteration.started":
            self._handle_iteration_started(payload)
        elif signal == "pipeline.map.iteration.failed":
            self._handle_iteration_failed(payload)
        elif signal == "pipeline.map.completed":
            self._handle_step_completed(payload)
        elif signal in {
            "federation.gateway.degraded",
            "federation.gateway.recovered",
        }:
            self._handle_gateway_signal(signal, payload)
        elif signal in {
            "model.loading.started",
            "model.loaded",
            "model.load.failed",
        }:
            self._handle_model_signal(signal, payload)

    async def _subscribe_loop(self) -> None:
        while True:
            connector = aiohttp.UnixConnector(path=_EVENT_QUERY_SOCK)
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(_SUBSCRIBE_PATH) as ws:
                        msg: dict[str, object] = {"type": "subscribe"}
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
                                    "ExtractionAdmissionGate(%s) subscribed "
                                    "(resumed_from=%s)",
                                    self._pipeline_id,
                                    event.get("resumed_from"),
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
                raise
            except Exception as exc:
                logger.warning(
                    "ExtractionAdmissionGate(%s) subscription error, "
                    "reconnecting in %.0fs: %s",
                    self._pipeline_id,
                    _RECONNECT_DELAY_S,
                    exc,
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)
