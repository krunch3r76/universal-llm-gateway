"""In-memory async build tracker for grokbuild-worker (Phase B).

Operator-decided contract (`decision:grokbuild-execution-tracker-shape`):

* 1a Storage: in-memory dict only. Restart → orphans purged by
  ``cleanup_orphans`` via pid-liveness check (``os.kill(pid, 0)``).
* 1b Retention TTL: 24 hours after terminal state. Status/result return
  404 once the TTL window closes.
* 1c Concurrent-build cap: 4 in-flight ``run_dispatch`` subprocesses
  max. Over cap → :class:`TrackerCapacityError` (route layer maps to
  HTTP 429 + ``Retry-After``). No queueing.
* 1d SSE event vocabulary: subset of Event Service ``grokbuild.dispatch.*``
  signals — accepted, started, progress, completed, cancelled. Tracker
  publishes to both the SSE per-listener fanout and the worker event
  bus so external consumers can correlate.

The tracker deliberately does NOT extend ``PipelineExecutionTracker`` —
pipeline tracker semantics are model-loading + pipeline-step-driven;
grokbuild is subprocess-lifecycle driven (different vocabularies,
different lifetimes, different failure modes).

Module split (V2 close-out, review C4):
* ``tracker_state``  — ``Entry``, ``TrackerCapacityError``, ``iso_now``,
  ``pid_alive``
* ``tracker_cancel`` — ``cancel_entry`` SIGTERM/SIGKILL flow
* ``tracker_runner`` — ``run_dispatch_task`` background-task body
* ``tracker``        — public ``GrokbuildExecutionTracker`` class
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from universal_logging import get_logger

from services.grokbuild_worker.events import (
    GrokbuildDispatchAcceptedEvent,
    GrokbuildDispatchCancelledEvent,
    GrokbuildTrackerOrphanCleaned,
    publish_nowait,
)
from services.grokbuild_worker.models.async_dispatch import GrokbuildDispatchRequest
from services.grokbuild_worker.tracker_cancel import cancel_entry
from services.grokbuild_worker.tracker_runner import run_dispatch_task
from services.grokbuild_worker.tracker_state import (
    Entry,
    TrackerCapacityError,
    iso_now,
    pid_alive,
)

logger = get_logger(__name__)

DEFAULT_CAPACITY = 4
DEFAULT_TTL_SECONDS = 24 * 3600

# Re-exports — kept on this module so legacy import paths
# (``from services.grokbuild_worker.tracker import _Entry``) survive the split.
_Entry = Entry  # noqa: N816 — legacy alias for tests / external imports
__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_TTL_SECONDS",
    "Entry",
    "GrokbuildExecutionTracker",
    "TrackerCapacityError",
    "_Entry",
]


class GrokbuildExecutionTracker:
    """Dedicated async tracker for grokbuild dispatches (see module docstring)."""

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._dispatches: dict[str, Entry] = {}
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def _running_count(self) -> int:
        return sum(
            1 for e in self._dispatches.values() if e.state in {"pending", "running"}
        )

    def _ttl_sweep(self) -> None:
        """Drop terminal entries past TTL (operator answer 1b)."""
        now = time.monotonic()
        expired = [
            did
            for did, e in self._dispatches.items()
            if e.is_terminal
            and e.completed_monotonic is not None
            and (now - e.completed_monotonic) > self._ttl_seconds
        ]
        for did in expired:
            self._dispatches.pop(did, None)
        if expired:
            logger.info("grokbuild tracker TTL-purged %d entries", len(expired))

    async def start(self, request: GrokbuildDispatchRequest) -> str:
        """Admit a dispatch, spawn the runner task, return the dispatch_id."""
        async with self._lock:
            self._ttl_sweep()
            running = self._running_count()
            if running >= self._capacity:
                raise TrackerCapacityError(running, self._capacity)
            dispatch_id = str(uuid.uuid4())
            entry = Entry(
                dispatch_id=dispatch_id,
                state="pending",
                request=request,
                started_at=iso_now(),
            )
            self._dispatches[dispatch_id] = entry

        publish_nowait(
            GrokbuildDispatchAcceptedEvent(
                dispatch_id=dispatch_id,
                model=request.model or "",
                worktree=request.cwd,
                requested_by="api",
            )
        )
        self.fanout(entry, {"type": "accepted", "dispatch_id": dispatch_id})

        task = asyncio.create_task(
            run_dispatch_task(self, entry),
            name=f"grokbuild-dispatch-{dispatch_id[:8]}",
        )
        entry.task = task
        return dispatch_id

    def fanout(self, entry: Entry, event: dict[str, Any]) -> None:
        """Push event to all live SSE subscribers and update entry state."""
        entry.last_event = event
        entry.updated_at = iso_now()
        if event.get("type") == "progress":
            entry.progress_summary = event.get("summary", entry.progress_summary)
        for q in list(entry.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def close_subscribers(self, entry: Entry) -> None:
        """Send the terminal sentinel to all live SSE subscribers."""
        for q in list(entry.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
        entry.subscribers.clear()

    async def status(self, dispatch_id: str) -> dict[str, Any] | None:
        """Return a status snapshot, or ``None`` if unknown / TTL-expired."""
        self._ttl_sweep()
        entry = self._dispatches.get(dispatch_id)
        if entry is None:
            return None
        return {
            "dispatch_id": entry.dispatch_id,
            "state": entry.state,
            "started_at": entry.started_at,
            "updated_at": entry.updated_at,
            "completed_at": entry.completed_at,
            "progress_summary": entry.progress_summary,
            "last_event": entry.last_event,
            "result_available": entry.state in {"succeeded", "failed"},
            "pid": entry.pid,
            "exit_code": entry.exit_code,
            "error": entry.error,
        }

    async def stream_events(self, dispatch_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield tracker events until terminal state; close on terminal."""
        entry = self._dispatches.get(dispatch_id)
        if entry is None:
            return
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=256)
        entry.subscribers.append(q)
        # Seed with a snapshot so late subscribers see current state immediately.
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(
                {
                    "type": "snapshot",
                    "state": entry.state,
                    "progress_summary": entry.progress_summary,
                }
            )
        if entry.is_terminal:
            outcome = (
                "cancelled"
                if entry.state == "cancelled"
                else ("success" if entry.state == "succeeded" else "external_failure")
            )
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(
                    {
                        "type": "completed",
                        "outcome": outcome,
                        "exit_code": entry.exit_code,
                    }
                )
            q.put_nowait(None)
        try:
            while True:
                event = await q.get()
                if event is None:
                    return
                yield event
        finally:
            # Client disconnect MUST NOT cancel the subprocess.
            if q in entry.subscribers:
                entry.subscribers.remove(q)

    async def cancel(self, dispatch_id: str) -> tuple[int, dict[str, Any]]:
        """Operator cancel: SIGTERM → 30s grace → SIGKILL.

        Body extracted to ``tracker_cancel.cancel_entry`` so the tracker
        class stays under the 300-SLOC ceiling. The event publish + 404
        lookup stay here because they touch tracker-private state.
        """
        entry = self._dispatches.get(dispatch_id)
        if entry is None:
            return 404, {"reason_code": "not_found", "reason": "unknown dispatch_id"}
        signal_used, status_code, body = await cancel_entry(entry)
        if signal_used != "noop":
            publish_nowait(
                GrokbuildDispatchCancelledEvent(
                    dispatch_id=dispatch_id,
                    reason="operator_cancel",
                    signal_used=signal_used,
                )
            )
        return status_code, body

    async def cleanup_orphans(self) -> int:
        """Purge tracker entries whose subprocess pid is no longer alive.

        Lifespan-startup hook. With pure-in-memory storage (operator answer
        1a) the live ``_dispatches`` dict is empty at boot — so this is
        usually a no-op. Tests pre-seed dead entries to exercise the path.
        """
        dead: list[str] = []
        for did, entry in list(self._dispatches.items()):
            if entry.is_terminal:
                continue
            entry_pid = entry.pid
            if entry_pid is None or not pid_alive(entry_pid):
                dead.append(did)
                entry.state = "failed"
                entry.error = "orphaned_at_boot"
                entry.completed_at = iso_now()
                entry.completed_monotonic = time.monotonic()
                entry.updated_at = entry.completed_at
        if dead:
            publish_nowait(
                GrokbuildTrackerOrphanCleaned(count=len(dead), dispatch_ids=dead)
            )
            for did in dead:
                self._dispatches.pop(did, None)
            logger.info("grokbuild tracker purged %d orphan(s) at boot", len(dead))
        return len(dead)

    async def drain(self, timeout_seconds: float = 30.0) -> None:
        """Lifespan-shutdown: wait for in-flight tasks then SIGTERM survivors."""
        tasks = [
            e.task for e in self._dispatches.values() if e.task and not e.task.done()
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            logger.warning(
                "grokbuild tracker drain timed out; SIGTERM-ing %d survivor(s)",
                sum(1 for t in tasks if not t.done()),
            )
            for entry in self._dispatches.values():
                survivor_pid = entry.pid
                if survivor_pid is not None and not (entry.task and entry.task.done()):
                    with contextlib.suppress(
                        ProcessLookupError, PermissionError, OSError
                    ):
                        os.killpg(os.getpgid(survivor_pid), signal.SIGTERM)

    def _seed_for_test(self, entry: Entry) -> None:
        self._dispatches[entry.dispatch_id] = entry
