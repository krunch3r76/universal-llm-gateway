"""
CapacityPool mixin: admission pause / resume for starvation-relief preemption.

When paused, new acquires wait; resume unblocks the FIFO waiter dispatch path.
"""

from __future__ import annotations

import asyncio
import time

from universal_logging import get_logger

logger = get_logger(__name__)


class _PauseMixin:
    def is_paused(self, model_id: str) -> bool:
        """Return True iff admission for model_id is currently suspended.

        Lazy expiration: checks the deadline on every call so callers never
        observe a pause that has already elapsed, even if the resume task
        has not yet fired.
        """
        deadline = self._paused_until.get(model_id)  # type: ignore[attr-defined]
        if deadline is None:
            return False
        if time.monotonic() >= deadline:
            self._clear_pause(model_id, reason="expired_lazy")
            return False
        return True

    def pause_admission(
        self,
        model_id: str,
        duration_s: float,
        reason: str = "starvation_relief",
    ) -> float:
        """Suspend admission for model_id for up to duration_s seconds.

        In-flight requests complete normally; the routing_key_tracker will
        eventually report no in-flight keys for model_id, at which point the
        eviction planner can classify the model as idle and evict it.

        Stacking semantics: if already paused, the deadline extends only if
        the new deadline is later than the existing one. Re-applying a shorter
        pause is a no-op. The reason of the longest pause wins.

        Schedules a single resume task that fires when the deadline elapses;
        the task calls _dispatch(model_id) so queued waiters wake up promptly.
        """
        if duration_s <= 0:
            return 0.0

        now = time.monotonic()
        new_deadline = now + duration_s
        existing = self._paused_until.get(model_id, 0.0)  # type: ignore[attr-defined]
        if new_deadline <= existing:
            return max(0.0, existing - now)

        was_paused = existing > now
        self._paused_until[model_id] = new_deadline  # type: ignore[attr-defined]
        self._paused_reason[model_id] = reason  # type: ignore[attr-defined]

        old_task = self._resume_tasks.pop(model_id, None)  # type: ignore[attr-defined]
        if old_task is not None and not old_task.done():
            old_task.cancel()

        try:
            self._resume_tasks[model_id] = asyncio.create_task(  # type: ignore[attr-defined]
                self._resume_after(model_id, duration_s),
                name=f"capacity-admission-resume-{model_id}",
            )
        except RuntimeError:
            logger.warning(
                "pause_admission: no running loop; resume for %s will rely on "
                "lazy expiration at next _try_immediate / _dispatch",
                model_id,
            )

        if was_paused:
            logger.info(
                "Admission pause EXTENDED for %s: +%.1fs "
                "(reason=%s, total_remaining=%.1fs)",
                model_id,
                duration_s,
                reason,
                new_deadline - now,
            )
        else:
            logger.warning(
                "Admission pause STARTED for %s: %.1fs (reason=%s)",
                model_id,
                duration_s,
                reason,
            )
        self._emit_admission_paused(model_id, duration_s, reason)  # type: ignore[attr-defined]
        return duration_s

    def resume_admission(
        self,
        model_id: str,
        reason: str = "explicit",
    ) -> bool:
        """Release an active pause and dispatch any queued waiters for model_id.

        Returns True iff a pause was active. Safe to call when no pause exists.
        """
        if model_id not in self._paused_until:  # type: ignore[attr-defined]
            return False
        self._clear_pause(model_id, reason=reason)
        try:
            asyncio.create_task(
                self._dispatch(model_id),  # type: ignore[attr-defined]
                name=f"capacity-admission-resume-dispatch-{model_id}",
            )
        except RuntimeError:
            logger.warning(
                "resume_admission: no running loop to dispatch %s; queued waiters "
                "will wake on the next external trigger",
                model_id,
            )
        return True

    def _clear_pause(self, model_id: str, *, reason: str) -> None:
        """Drop all pause state for model_id and emit a resume event."""
        was_paused = model_id in self._paused_until  # type: ignore[attr-defined]
        self._paused_until.pop(model_id, None)  # type: ignore[attr-defined]
        self._paused_reason.pop(model_id, None)  # type: ignore[attr-defined]
        task = self._resume_tasks.pop(model_id, None)  # type: ignore[attr-defined]
        if task is not None and not task.done():
            task.cancel()
        if was_paused:
            logger.warning(
                "Admission pause RESUMED for %s (reason=%s)",
                model_id,
                reason,
            )
            self._emit_admission_resumed(model_id, reason)  # type: ignore[attr-defined]

    async def _resume_after(self, model_id: str, duration_s: float) -> None:
        """Wait out a pause deadline then dispatch queued waiters.

        Cancellation via pause_admission (extended) or resume_admission
        (explicit early release) is expected; silently exit in that case.
        """
        try:
            await asyncio.sleep(duration_s)
        except asyncio.CancelledError:
            return
        deadline = self._paused_until.get(model_id)  # type: ignore[attr-defined]
        if deadline is None or time.monotonic() < deadline:
            return
        self._clear_pause(model_id, reason="ttl_expired")
        await self._dispatch(model_id)  # type: ignore[attr-defined]
