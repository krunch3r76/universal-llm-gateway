"""In-process Auto handler registration + heartbeat for arm predicate.

``handler_status=auto-admit-armed`` requires a live registered handler — a
successful turn write alone is not arm evidence (R-admit HIGH / F1).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from deploy_identity.code_version import resolve_code_version

from services.git_integration_worker.cursor_auto.wire_skew_events import (
    get_wire_skew_aggregate,
)


@dataclass
class AutoLivenessRegistry:
    """Process-local registry of live Auto handlers for ``lane:cursor-auto``."""

    heartbeat_ttl_s: float = 30.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _handlers: dict[str, float] = field(default_factory=dict)
    _started_at: float = field(default_factory=time.monotonic)

    def register(self, handler_id: str) -> None:
        """Register or refresh a live Auto handler heartbeat."""
        with self._lock:
            self._handlers[handler_id] = time.monotonic()

    def heartbeat(self, handler_id: str) -> bool:
        """Refresh heartbeat; re-register if pruned while a long job held the loop.

        Mid-job silence can exceed ``heartbeat_ttl_s``; ``is_live``/snapshot prune
        the id. A strict miss-return here left the lane permanently dead after the
        first nested SDK job (5867 DIRECTIVE-4 / dead-handler friction).
        """
        with self._lock:
            existed = handler_id in self._handlers
            self._handlers[handler_id] = time.monotonic()
            return existed

    def unregister(self, handler_id: str) -> None:
        with self._lock:
            self._handlers.pop(handler_id, None)

    def _prune_locked(self, now: float) -> None:
        stale = [
            hid
            for hid, ts in self._handlers.items()
            if (now - ts) > self.heartbeat_ttl_s
        ]
        for hid in stale:
            del self._handlers[hid]

    def is_live(self) -> bool:
        """True when ≥1 handler has a fresh heartbeat within TTL."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            return bool(self._handlers)

    def snapshot(self) -> dict[str, Any]:
        """Liveness snapshot for `/cursor-auto/liveness` and arm probes."""
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            handlers = {
                hid: {"age_s": round(now - ts, 3)} for hid, ts in self._handlers.items()
            }
        return {
            "live": bool(handlers),
            "lane": "cursor-auto",
            "handler_count": len(handlers),
            "handlers": handlers,
            "heartbeat_ttl_s": self.heartbeat_ttl_s,
            "uptime_s": round(now - self._started_at, 3),
            "pid": os.getpid(),
            "code_version": resolve_code_version(),
            "wire_skew_aggregate": get_wire_skew_aggregate(),
        }


_REGISTRY = AutoLivenessRegistry()


def get_registry() -> AutoLivenessRegistry:
    """Return the process-global Auto liveness registry."""
    return _REGISTRY


_OCCUPANT_IDLE_RED_THRESHOLD_S = 90.0  # 45x the ~2s heartbeat cadence --
# generous margin against event-loop hiccups/GC pauses while still catching
# a genuinely stuck occupant well inside an operator's patience window.
# Named constant -- retune here only, no call-site changes needed.

_last_queue_red: bool = False


def reset_queue_health_red_edge_for_tests() -> None:
    """Clear the process-local rising-edge latch so a test can observe a fresh emit."""
    global _last_queue_red
    _last_queue_red = False


def queue_admission_health() -> dict[str, Any]:
    """Admit-eligible pending depth + queue-not-serving red projection (S-4).

    PROJECTION ONLY: this function must never call mark_done / mark_terminal
    / mark_superseded or otherwise mutate a job. It exists to be read by
    ``/liveness`` and by humans/dashboards -- nothing may key an automatic
    reap or terminalize decision off ``red`` here. If a future slice wants to
    act on this signal, that is a new, separate, explicitly-scoped change --
    not an extension of this function.

    ``red`` answers "is the queue serving?", not "is the occupant working?".
    Occupant progress stays on ``occupant_idle_s``. Cause lives on
    ``red_reason`` (``occupant_idle`` / ``waiter_starvation`` /
    ``occupant_idle_and_waiter_starvation`` / None).

    ``red :=`` occupant heartbeat stale past threshold with waiters
    **OR** waiter starvation (``amber``). A heartbeating occupant with a
    fresh waiter stays green — that is a legitimate long job, not a stall.
    """
    from services.git_integration_worker.cursor_auto.execution_mode import (
        is_concurrent_execution_mode,
    )
    from services.git_integration_worker.cursor_auto.job_ledger import get_ledger
    from services.git_integration_worker.cursor_auto.waiter_visibility import (
        waiter_starvation_from_conn,
    )

    ledger = get_ledger()
    open_jobs = ledger.list_open()
    admit_eligible_pending = [
        j
        for j in open_jobs
        if j.status == "queued" and not is_concurrent_execution_mode(j.execution_mode)
    ]
    serial_occupant = next(
        (
            j
            for j in open_jobs
            if j.status == "claimed"
            and not j.continuity_hop
            and not is_concurrent_execution_mode(j.execution_mode)
        ),
        None,
    )
    occupant_idle_s: float | None = None
    occupant_stalled = False
    if serial_occupant is not None:
        occupant_idle_s = ledger.heartbeat_age_s(serial_occupant.job_id)
        occupant_stalled = bool(
            admit_eligible_pending
            and occupant_idle_s is not None
            and occupant_idle_s > _OCCUPANT_IDLE_RED_THRESHOLD_S
        )
    with ledger._connect() as conn:
        waiter = waiter_starvation_from_conn(conn)
    waiter_starved = bool(waiter.get("amber"))
    red = occupant_stalled or waiter_starved
    if occupant_stalled and waiter_starved:
        red_reason: str | None = "occupant_idle_and_waiter_starvation"
    elif occupant_stalled:
        red_reason = "occupant_idle"
    elif waiter_starved:
        red_reason = "waiter_starvation"
    else:
        red_reason = None
    snapshot = {
        "admit_eligible_pending": len(admit_eligible_pending),
        "serial_occupant_job_id": (
            serial_occupant.job_id if serial_occupant is not None else None
        ),
        "occupant_idle_s": (
            round(occupant_idle_s, 3) if occupant_idle_s is not None else None
        ),
        "red": red,
        "red_reason": red_reason,
        "red_threshold_s": _OCCUPANT_IDLE_RED_THRESHOLD_S,
        "projection_only": True,
        **waiter,
    }
    _emit_queue_not_serving_rising_edge(snapshot)
    return snapshot


def _emit_queue_not_serving_rising_edge(snapshot: dict[str, Any]) -> None:
    """Emit once when ``red`` rises; never on the hot poll while already red."""
    global _last_queue_red
    red = bool(snapshot.get("red"))
    rising = red and not _last_queue_red
    _last_queue_red = red
    if not rising:
        return
    from services.git_integration_worker.cursor_auto.queue_health_events import (
        emit_queue_not_serving,
    )

    emit_queue_not_serving(
        red_reason=str(snapshot.get("red_reason") or "unknown"),
        admit_eligible_pending=int(snapshot.get("admit_eligible_pending") or 0),
        oldest_waiter_age_s=snapshot.get("oldest_waiter_age_s"),
        occupant_idle_s=snapshot.get("occupant_idle_s"),
        serial_occupant_job_id=snapshot.get("serial_occupant_job_id"),
    )
