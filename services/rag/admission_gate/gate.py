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
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from model_id import ModelId

from ._io import _emit_first_burst_observed, _snapshot, _subscribe_loop
from ._signals import _apply_signal

if TYPE_CHECKING:
    from universal_event_bus import EventBus

logger = logging.getLogger(__name__)


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

    def __init__(
        self, model_ids: Iterable[str], *, event_bus: EventBus | None = None
    ) -> None:
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
        self._event_bus: EventBus | None = event_bus
        # First-burst measurement (todo:rag-admission-gate-first-burst-measurement).
        # Counts workers that passed wait_for_admission() while OPEN since last
        # gate reset; reset to 0 on each OPEN transition.
        self._workers_admitted: dict[str, int] = {key: 0 for key in self._tracked}
        # Guard: emit rag.admission.first.burst.observed exactly once per model.
        self._first_burst_emitted: set[str] = set()

    async def start(self) -> None:
        """Configure from snapshot then spawn the background subscriber. Idempotent.

        Implements the configure→snapshot→subscribe lifecycle (mirrors
        ModelAvailabilityTracker).  Before subscribing to the Event Service
        WebSocket, fetches GET /api/v1/admission/state?model_id=<routing_key>
        from Stargate for every tracked model and pre-seeds gate state so the
        startup-snapshot race is closed at startup rather than waiting for the
        next signal cycle.

        Snapshot semantics:
          loading or paused → CLOSE the gate (workers should wait)
          otherwise         → leave SET (default OPEN — workers may submit)

        Snapshot is best-effort: if Stargate is unreachable or returns an
        error, a warning is logged and the gate defaults OPEN.  The per-request
        X-Request-Timeout enforced server-side remains the correctness backstop.

        The remaining first-batch cold-load race (N workers passing through
        before model.loading.started round-trips) is still accepted as bounded
        — see `todo:rag-admission-gate-first-burst-measurement` and the
        Worst-Case Cold-Load Timing section in phase4.md.
        """
        await _snapshot(self)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(
                _subscribe_loop(self), name="rag-admission-gate"
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

        Every call that allows a worker through (True return or timeout
        fallback) increments the per-model counter used by the first-burst
        measurement (todo:rag-admission-gate-first-burst-measurement).
        """
        key = ModelId.parse(model_id).routing_key
        ev = self._tracked.get(key)
        if ev is None:
            return True
        if ev.is_set():
            self._workers_admitted[key] = self._workers_admitted.get(key, 0) + 1
            return True
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "AdmissionGate timed out waiting for %s "
                "(proceeding; per-chunk timeout will backstop)",
                key,
            )
            self._workers_admitted[key] = self._workers_admitted.get(key, 0) + 1
            return False
        self._workers_admitted[key] = self._workers_admitted.get(key, 0) + 1
        return True

    def _apply_signal(self, signal: str, payload: dict[str, object]) -> None:
        _apply_signal(self, signal, payload)

    async def _emit_first_burst_observed(
        self, key: str, workers_in_flight: int
    ) -> None:
        await _emit_first_burst_observed(self, key, workers_in_flight)
