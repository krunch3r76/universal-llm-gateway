"""Mixin: request cancellation, waiter removal, and leaked-slot recovery."""

from __future__ import annotations

import asyncio
import time

from universal_logging import get_logger

from ._types import _Slot, _WaiterCancelledError

logger = get_logger(__name__)


class _CancellationMixin:
    def cancel_request(self, request_id: str, reason: str = "explicit_cancel") -> bool:
        """Remove a waiter from its per-model queue by request_id.

        Returns True iff a queued waiter was found and removed. Explicit
        cancellation wakes the waiting task via a typed exception so callers can
        distinguish "cancelled while queued" from a real queue error.
        """
        return self._remove_waiter(
            request_id,
            reason=reason,
            wake_with_exception=True,
        )

    def _remove_waiter(
        self,
        request_id: str,
        *,
        reason: str,
        wake_with_exception: bool,
    ) -> bool:
        """Remove a queued waiter and wake it according to the cancellation source."""
        for model_id, queue in list(self._queues.items()):  # type: ignore[attr-defined]
            for i, waiter in enumerate(queue):
                if waiter.request_id == request_id:
                    del queue[i]
                    if not waiter.future.done():
                        if wake_with_exception:
                            waiter.future.set_exception(_WaiterCancelledError(reason))
                        else:
                            waiter.future.cancel()
                    if not queue:
                        del self._queues[model_id]  # type: ignore[attr-defined]
                    wait_ms = (time.monotonic() - waiter.queued_at) * 1000
                    self._emit_queue_cancelled(request_id, model_id, wait_ms, reason)  # type: ignore[attr-defined]
                    return True
        return False

    def _recover_leaked_slot(
        self, request_id: str, gateway_id: str, model_id: str
    ) -> None:
        """
        Release a slot that was reserved by _dispatch but never claimed as a token.

        Called when CancelledError/TimeoutError interrupts _wait_for_slot AFTER
        the future was resolved (dispatch already incremented in_flight).
        Without this recovery, in_flight is permanently stuck and the slot
        is blocked for all future requests.
        """
        slot = _Slot(gateway_id=gateway_id, model_id=model_id)
        in_flight = self._in_flight.get(slot, 0)  # type: ignore[attr-defined]
        if in_flight <= 0:
            logger.error(
                f"Leaked slot recovery: {request_id} on {gateway_id}/{model_id} "
                f"but in_flight already 0 — possible double-recovery"
            )
            return
        self._in_flight[slot] = in_flight - 1  # type: ignore[attr-defined]
        logger.warning(
            f"Recovered leaked slot: {request_id} on {gateway_id}/{model_id} "
            f"(admitted by dispatch, cancelled before token creation, "
            f"in_flight: {in_flight} → {in_flight - 1})"
        )
        self._emit_slot_leak_recovered(request_id, gateway_id, model_id)  # type: ignore[attr-defined]
        # Wake next waiter in a new task — cannot await during cancellation
        try:
            asyncio.create_task(
                self._dispatch(model_id),  # type: ignore[attr-defined]
                name=f"capacity-recover-dispatch-{model_id}",
            )
        except RuntimeError as exc:
            logger.warning(
                "Failed to dispatch after slot leak recovery for %s: "
                "no running event loop (%s)",
                model_id,
                exc,
            )
