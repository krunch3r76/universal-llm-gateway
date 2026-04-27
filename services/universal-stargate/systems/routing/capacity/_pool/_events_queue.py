"""Mixin: emit capacity queue lifecycle events (entered, admitted, waiting, full, cancelled)."""

from __future__ import annotations

import asyncio

from universal_logging import get_logger

logger = get_logger(__name__)


class _EventsQueueMixin:
    def _emit_queue_entered(
        self,
        request_id: str,
        model_id: str,
        queue_position: int,
        allowed_gateways: int,
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityPoolQueued

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityPoolQueued(
                        request_id=request_id,
                        model_id=model_id,
                        queue_position=queue_position,
                        allowed_gateways=allowed_gateways,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.pool.queued: %s", exc)

    def _emit_queue_admitted(
        self,
        request_id: str,
        model_id: str,
        gateway_id: str,
        wait_ms: float,
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityPoolAdmitted

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityPoolAdmitted(
                        request_id=request_id,
                        model_id=model_id,
                        gateway_id=gateway_id,
                        wait_ms=wait_ms,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.pool.admitted: %s", exc)

    def _emit_queue_waiting(
        self,
        request_id: str,
        model_id: str,
        wait_ms: float,
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        queue = self._queues.get(model_id)  # type: ignore[attr-defined]
        if not queue:
            return
        queue_depth = len(queue)
        queue_position = next(
            (
                idx
                for idx, waiter in enumerate(queue, start=1)
                if waiter.request_id == request_id
            ),
            None,
        )
        if queue_position is None:
            return
        try:
            from src.scheduling.events import CapacityPoolWaiting

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityPoolWaiting(
                        request_id=request_id,
                        model_id=model_id,
                        wait_ms=wait_ms,
                        queue_position=queue_position,
                        queue_depth=queue_depth,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.pool.waiting: %s", exc)

    def _emit_queue_full(
        self, request_id: str, model_id: str, current_depth: int
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityPoolFull

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityPoolFull(
                        request_id=request_id,
                        model_id=model_id,
                        current_depth=current_depth,
                        max_depth=self._max_queue_depth,  # type: ignore[attr-defined]
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.pool.full: %s", exc)

    def _emit_queue_cancelled(
        self,
        request_id: str,
        model_id: str,
        wait_ms: float,
        reason: str,
    ) -> None:
        if not self._event_bus:  # type: ignore[attr-defined]
            return
        try:
            from src.scheduling.events import CapacityPoolCancelled

            asyncio.create_task(
                self._event_bus.publish_nowait(  # type: ignore[attr-defined]
                    CapacityPoolCancelled(
                        request_id=request_id,
                        model_id=model_id,
                        wait_ms=wait_ms,
                        reason=reason,
                    )
                )
            )
        except Exception as exc:
            logger.warning("Failed to emit capacity.pool.cancelled: %s", exc)
