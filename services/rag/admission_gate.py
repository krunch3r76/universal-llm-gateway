"""Event-driven admission gate for batch contextualization workers.

Subscribes to Stargate's coordination-role signals over the Event Service
WebSocket (UDS) — both `capacity.admission.*` (starvation-drain
preemption) and `model.loading.started` / `model.loaded` /
`model.load.failed` (cold-load coordination). Maintains a per-model
`asyncio.Event` workers can await before submitting work.

Per the stargate-model-lifecycle invariant this remains coordination only —
the per-chunk client timeout (now properly enforced server-side by
Stargate's X-Request-Timeout) is the correctness backstop. A subscriber
that misses signals degrades to no-op (default OPEN), never to incorrect
behavior.

Replaces the previous ContextualizeModelCoordinator (deleted), which
filtered `model.*` lifecycle events but missed signals because Stargate
normalized model IDs (e.g. configured `qwen3-5-9b-q8-0-262144` while
events arrived for `qwen3-5-9b-q8-0-131072`). Capacity admission signals
do not have that normalization issue because they fire from the same
routing layer the request was admitted to, with the routing_key Stargate
actually used; the model lifecycle signals are also re-checked here under
the routing_key, with a divergence detector to surface any mismatch
between subscribed keys and observed signals.

Subscribed signals (role=coordination):
  - capacity.admission.paused   → mark gate CLOSED (starvation_drain
                                  preemption — fires when a competing
                                  starved model needs the slot)
  - capacity.admission.resumed  → mark gate OPEN
  - model.loading.started       → mark gate CLOSED (cold-load window
                                  for the target model itself; this is
                                  the signal that capacity.admission.*
                                  alone does NOT cover — see
                                  Worst-Case Cold-Load Timing in
                                  phase4.md)
  - model.loaded                → mark gate OPEN (cold load completed)
  - model.load.failed           → mark gate OPEN (restore optimism so
                                  the next worker's request triggers a
                                  retry; Stargate re-loads on demand
                                  and that request fails loudly, which
                                  is the correctness signal — preserved
                                  from the previous
                                  ContextualizeModelCoordinator)
  - federation.gateway.degraded → mark gate CLOSED for all tracked models
                                  while a federated gateway is timing out
  - federation.gateway.recovered→ mark gate OPEN only after the degraded
                                  gateway has recovered and no other
                                  close reason remains

Intentionally NOT subscribed:
  - model.unloaded — a bare unload (eviction) without a subsequent
    model.loading.started does not mean "workers should pause"; the
    very next contextualize request will trigger Stargate to reload
    the model on demand. Pausing here would force every batch to wait
    the full client_timeout_s for a model.loaded that may never come
    if the upstream emission chain has a gap.

Bounded first-burst behavior:
  AdmissionGate defaults OPEN (see start() docstring). When the
  contextualize model is cold and N workers wake up simultaneously,
  all N pass through the open gate before model.loading.started can
  round-trip from Stargate. The first request triggers Stargate's
  cold-load placeholder capacity (loading_phase_cap=1 by default);
  the remaining N−1 enter Stargate's capacity_pool FIFO queue and
  drain when the model loads. This is acceptable: N is bounded by
  per-file workers × concurrent files (typically ≤ 32–64) and
  CapacityPool was designed to absorb that bound. Subsequent batches
  see the gate CLOSED via model.loading.started and wait there
  instead of stampeding. See the Worst-Case Cold-Load Timing section
  in phase4.md for the full design walkthrough.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable

import aiohttp
from model_id import ModelId

logger = logging.getLogger(__name__)

_EVENT_QUERY_SOCK = os.environ.get(
    "EVENTS_QUERY_SOCK", "/tmp/universal-protocol/events-query.sock"
)
_SUBSCRIBE_PATH = "http://localhost/v1/subscribe"  # host ignored with UDS
_RECONNECT_DELAY_S = 5.0


class AdmissionGate:
    """Per-model admission gate driven by Stargate capacity signals.

    State per tracked model:
      - asyncio.Event SET   ⇒ admission OPEN; workers may submit
      - asyncio.Event CLEAR ⇒ admission CLOSED; workers should wait

    Defaults SET. CLOSE on capacity.admission.paused (starvation_drain),
    model.loading.started (cold-load window for the target model itself),
    or federation.gateway.degraded (remote gateway timeout window). OPEN
    when the corresponding close reason is removed by
    capacity.admission.resumed, model.loaded, model.load.failed, or
    federation.gateway.recovered. A worker that times out waiting proceeds
    optimistically — admission is coordination, not correctness; the
    per-request X-Request-Timeout enforced server-side is the correctness
    backstop.

    Tracking key is `ModelId.routing_key` of the configured model; signals
    are matched by parsing the payload's `model_id` and comparing
    `.routing_key`. This avoids the suffix/normalization bugs that broke
    the previous coordinator.
    """

    def __init__(self, model_ids: Iterable[str]) -> None:
        self._tracked: dict[str, asyncio.Event] = {}
        self._closed_reasons: dict[str, set[str]] = {}
        for raw in model_ids:
            if not raw:
                continue
            key = ModelId.parse(raw).routing_key
            ev = asyncio.Event()
            ev.set()
            self._tracked[key] = ev
            self._closed_reasons[key] = set()
        self._task: asyncio.Task[None] | None = None
        self._last_seq: int | None = None
        # Divergence detector: routing_keys we've seen on a relevant signal
        # but don't track. Logged once per key to surface model_id mismatch
        # at runtime without spamming the log on every cold load of unrelated
        # models. See module docstring "model_id divergence detection".
        self._unknown_seen: set[str] = set()

    def start(self) -> None:
        """Spawn the background subscriber task. Idempotent.

        Default state for every tracked model is OPEN. Two known races
        leave the gate wrong until the next signal corrects it:

        1. Startup-snapshot race: if Stargate paused admission OR the
           model was mid-cold-load BEFORE RAG started, the gate is wrong
           until the next paused→resumed or loading→loaded cycle.

        2. First-batch cold-load race: if N workers wake up while the
           target model is cold and the gate is OPEN, all N pass through
           before model.loading.started can round-trip from Stargate.
           The first request is admitted (loading_phase_cap=1); the rest
           queue at Stargate's CapacityPool. See "Worst-Case Cold-Load
           Timing" in phase4.md for why this is bounded and acceptable.

        Both races are safe (correctness held by the per-request
        X-Request-Timeout) and tracked as
        `todo:rag-admission-gate-startup-snapshot` for the longer-term
        configure→snapshot→subscribe lifecycle that mirrors
        ModelAvailabilityTracker. Implementation requires a new
        Stargate endpoint (GET /api/v1/admission/state?model_id=...) so
        is intentionally out of scope here.
        """
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                self._subscribe_loop(), name="rag-admission-gate"
            )

    async def stop(self) -> None:
        """Cancel the subscriber task."""
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait({self._task})
            self._task = None

    async def wait_for_admission(self, model_id: str, timeout: float) -> bool:
        """Wait until admission for model_id is OPEN, capped by timeout.

        Returns True if admission is OPEN (or the model is untracked);
        False on timeout. Callers MUST proceed regardless — admission is a
        hint, not a correctness gate.
        """
        key = ModelId.parse(model_id).routing_key
        ev = self._tracked.get(key)
        if ev is None:
            return True
        if ev.is_set():
            return True
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "AdmissionGate timed out waiting for %s "
                "(proceeding; per-chunk timeout will backstop)",
                key,
            )
            return False
        return True

    def _close_gate(
        self,
        key: str,
        *,
        reason: str,
        signal: str,
        payload: dict[str, object],
    ) -> None:
        ev = self._tracked[key]
        reasons = self._closed_reasons.setdefault(key, set())
        already_closed_for_reason = reason in reasons
        reasons.add(reason)
        if ev.is_set():
            ev.clear()
            logger.info(
                "AdmissionGate: %s CLOSED (signal=%s, reason=%s, active_reasons=%s)",
                key,
                signal,
                payload.get("reason", reason),
                sorted(reasons),
            )
        elif not already_closed_for_reason:
            logger.info(
                "AdmissionGate: %s remains CLOSED (signal=%s, reason=%s, active_reasons=%s)",
                key,
                signal,
                payload.get("reason", reason),
                sorted(reasons),
            )

    def _open_gate(
        self,
        key: str,
        *,
        reason: str,
        signal: str,
        payload: dict[str, object],
    ) -> None:
        ev = self._tracked[key]
        reasons = self._closed_reasons.setdefault(key, set())
        reasons.discard(reason)
        if not reasons and not ev.is_set():
            ev.set()
            logger.info(
                "AdmissionGate: %s OPEN (signal=%s, reason=%s)",
                key,
                signal,
                payload.get("reason", reason),
            )

    def _apply_gateway_signal(self, signal: str, payload: dict[str, object]) -> bool:
        if signal not in (
            "federation.gateway.degraded",
            "federation.gateway.recovered",
        ):
            return False
        raw_gateway = payload.get("gateway_id")
        if not isinstance(raw_gateway, str) or not raw_gateway:
            return True
        reason = f"gateway:{raw_gateway}"
        for key in self._tracked:
            if signal == "federation.gateway.degraded":
                self._close_gate(
                    key,
                    reason=reason,
                    signal=signal,
                    payload=payload,
                )
            else:
                self._open_gate(
                    key,
                    reason=reason,
                    signal=signal,
                    payload=payload,
                )
        return True

    def _apply_signal(self, signal: str, payload: dict[str, object]) -> None:
        if self._apply_gateway_signal(signal, payload):
            return

        raw_model = payload.get("model_id")
        if not isinstance(raw_model, str):
            return
        try:
            key = ModelId.parse(raw_model).routing_key
        except (ValueError, TypeError):
            return
        ev = self._tracked.get(key)
        if ev is None:
            # model_id divergence detection: the residual risk after the
            # ContextualizeModelCoordinator removal is that the catalog
            # routing layer (for capacity.admission.*) or gateway worker
            # telemetry (for model.*) ever reports a routing_key that
            # differs from RagConfig.contextualize_model. Log once per
            # unknown key on a relevant signal so operators can spot the
            # mismatch from logs without resolving the architectural
            # question prospectively. Unrelated model loads are logged
            # exactly once and then go silent.
            if (
                signal
                in (
                    "capacity.admission.paused",
                    "capacity.admission.resumed",
                    "model.loaded",
                    "model.loading.started",
                    "model.load.failed",
                )
                and key not in self._unknown_seen
            ):
                self._unknown_seen.add(key)
                logger.warning(
                    "AdmissionGate received %s for untracked model %s; "
                    "configured tracking: %s. If this looks like a variant "
                    "of a tracked model (different context-length suffix or "
                    "normalization), investigate model_id mismatch between "
                    "RagConfig.contextualize_model and the request path.",
                    signal,
                    key,
                    sorted(self._tracked.keys()),
                )
            return
        # CLOSE gate signals: admission paused (starvation_drain) OR model
        # cold-loading started. Both indicate workers should hold off.
        if signal in ("capacity.admission.paused", "model.loading.started"):
            reason = (
                "capacity.admission"
                if signal == "capacity.admission.paused"
                else "model.loading"
            )
            self._close_gate(
                key,
                reason=reason,
                signal=signal,
                payload=payload,
            )
        # OPEN gate signals:
        #   - capacity.admission.resumed: drain window ended
        #   - model.loaded: cold load completed
        #   - model.load.failed: restore optimism so the next worker request
        #     triggers a retry (Stargate re-loads on demand and that request
        #     fails loudly, which is the correctness signal). Without this
        #     branch, a failed cold load leaves the gate CLOSED until each
        #     waiting worker hits its full client_timeout_s. Preserved from
        #     the deleted ContextualizeModelCoordinator.
        elif signal in (
            "capacity.admission.resumed",
            "model.loaded",
            "model.load.failed",
        ):
            reason = (
                "capacity.admission"
                if signal == "capacity.admission.resumed"
                else "model.loading"
            )
            self._open_gate(
                key,
                reason=reason,
                signal=signal,
                payload=payload,
            )

    async def _subscribe_loop(self) -> None:
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
                                    "AdmissionGate subscribed (resumed_from=%s, models=%s)",
                                    event.get("resumed_from"),
                                    sorted(self._tracked.keys()),
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
                    "AdmissionGate subscription error, reconnecting in %.0fs: %s",
                    _RECONNECT_DELAY_S,
                    exc,
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)
