"""Mixin: emit admission lifecycle events (paused, resumed, slot leak recovered)."""

from __future__ import annotations

import asyncio

from universal_logging import get_logger

logger = get_logger(__name__)


class _EventsAdmissionMixin:
    def _emit_admission_paused(
        self, model_id: str, duration_s: float, reason: str
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityAdmissionPaused

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityAdmissionPaused(
                        model_id=model_id,
                        duration_s=duration_s,
                        reason=reason,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.admission.paused: %s", exc)

    def _emit_admission_resumed(self, model_id: str, reason: str) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityAdmissionResumed

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityAdmissionResumed(
                        model_id=model_id,
                        reason=reason,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.admission.resumed: %s", exc)

    def _emit_slot_leak_recovered(
        self, request_id: str, gateway_id: str, model_id: str
    ) -> None:
        """Emit capacity.slot.leak.recovered canary signal via event bus.

        Any occurrence indicates the cancellation race in _wait_for_slot was hit
        under load: _dispatch resolved the future (incrementing in_flight) but the
        waiter's task was cancelled before a CapacityToken was created.  The slot
        was recovered by _recover_leaked_slot; this signal makes the recovery
        observable for monitoring.
        """
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacitySlotLeakRecovered

            event = CapacitySlotLeakRecovered(
                request_id=request_id,
                gateway_id=gateway_id,
                model_id=model_id,
                snapshot=self.get_snapshot(),  # type: ignore[attr-defined]
            )
            asyncio.create_task(self._event_bus.publish_nowait(event))  # type: ignore[attr-defined]
        except RuntimeError as exc:
            logger.warning(
                "Failed to emit slot leak recovered event for %s/%s: "
                "no running event loop (%s)",
                gateway_id,
                model_id,
                exc,
            )
        except Exception as exc:
            logger.error(
                "Failed to emit slot leak recovered event for %s/%s: %s",
                gateway_id,
                model_id,
                exc,
                exc_info=True,
            )
