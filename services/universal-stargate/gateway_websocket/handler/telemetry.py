"""
Handler for Gateway compute capacity telemetry.

Aggregates queue wait events for orchestration observability.
Logs warnings when queue waits exceed threshold (orchestration drift).

Invariant: ∀ queue_wait_event: logged ∧ metrics_updated
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from universal_logging import get_logger

from .base import SyncMessageHandler
from .context import HandlerContext

logger = get_logger(__name__)

# Alert threshold: log WARN if queue wait exceeds this
QUEUE_WAIT_WARN_THRESHOLD_MS = 1000.0


@dataclass(slots=True, kw_only=True)
class QueueWaitMetrics:
    """Aggregated metrics for a gateway's queue waits."""

    total_queue_events: int = 0
    total_wait_ms: float = 0.0
    max_wait_ms: float = 0.0
    max_queue_position: int = 0  # Track deepest queue position seen
    last_event_timestamp: float = 0.0

    # Per compute type
    by_compute_type: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class ComputeCapacityTelemetryHandler:
    """
    Handles compute capacity telemetry from Gateway.

    Purpose:
    - Log when Gateway queues requests (orchestration out of sync)
    - Aggregate metrics for dashboards
    - Alert when queue waits exceed threshold
    """

    def __init__(self, gateway_id: str):
        self._gateway_id = gateway_id
        self._metrics = QueueWaitMetrics()

    def handle_queue_wait(self, payload: dict[str, Any]) -> None:
        """
        Handle COMPUTE_CAPACITY_QUEUE_WAIT event.

        Logs that a request is queuing at Gateway (orchestration drift signal).
        Uses strict field access — missing fields indicate wire protocol mismatch.
        """
        # Strict field access — fail loudly on missing fields
        request_id = payload["request_id"][:8]
        model_id = payload["model_id"]
        compute_type = payload["compute_type"]
        queue_position = payload["queue_position"]
        active = payload["active_count"]
        limit = payload["limit"]

        self._metrics.total_queue_events += 1
        self._metrics.by_compute_type[compute_type] += 1
        self._metrics.max_queue_position = max(
            self._metrics.max_queue_position, queue_position
        )
        self._metrics.last_event_timestamp = time.time()

        # Log as INFO (not an error, but noteworthy for orchestration review)
        logger.info(
            "📊 [TELEMETRY:%s] Gateway queue wait: request=%s model=%s "
            "compute=%s position=%d active=%d/%d",
            self._gateway_id,
            request_id,
            model_id,
            compute_type,
            queue_position,
            active,
            limit,
        )

    def handle_queue_acquired(self, payload: dict[str, Any]) -> None:
        """
        Handle COMPUTE_CAPACITY_QUEUE_ACQUIRED event.

        Logs queue wait duration. Warns if exceeds threshold.
        Uses strict field access — missing fields indicate wire protocol mismatch.
        """
        # Strict field access — fail loudly on missing fields
        request_id = payload["request_id"][:8]
        model_id = payload["model_id"]
        compute_type = payload["compute_type"]
        wait_ms = payload["wait_duration_ms"]
        queue_position = payload["queue_position_at_enqueue"]

        self._metrics.total_wait_ms += wait_ms
        self._metrics.max_wait_ms = max(self._metrics.max_wait_ms, wait_ms)

        if wait_ms > QUEUE_WAIT_WARN_THRESHOLD_MS:
            # Orchestration significantly out of sync
            logger.warning(
                "⚠️ [TELEMETRY:%s] Gateway queue wait exceeded threshold: "
                "request=%s model=%s compute=%s wait=%.0fms position=%d",
                self._gateway_id,
                request_id,
                model_id,
                compute_type,
                wait_ms,
                queue_position,
            )
        else:
            logger.debug(
                "📊 [TELEMETRY:%s] Gateway queue acquired: request=%s wait=%.0fms",
                self._gateway_id,
                request_id,
                wait_ms,
            )

    @property
    def metrics(self) -> QueueWaitMetrics:
        """Current aggregated metrics."""
        return self._metrics

    def get_stats(self) -> dict[str, Any]:
        """Stats dict for API/debugging."""
        return {
            "gateway_id": self._gateway_id,
            "total_queue_events": self._metrics.total_queue_events,
            "total_wait_ms": self._metrics.total_wait_ms,
            "max_wait_ms": self._metrics.max_wait_ms,
            "max_queue_position": self._metrics.max_queue_position,
            "avg_wait_ms": (
                self._metrics.total_wait_ms / self._metrics.total_queue_events
                if self._metrics.total_queue_events > 0
                else 0.0
            ),
            "by_compute_type": dict(self._metrics.by_compute_type),
        }


class ComputeQueueWaitHandler(SyncMessageHandler):
    """
    Handle COMPUTE_QUEUE_WAIT WebSocket message.

    Delegates to ComputeCapacityTelemetryHandler for aggregation.
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        handler = ctx.get_capacity_telemetry_handler()
        if handler:
            handler.handle_queue_wait(data)


class ComputeQueueAcquiredHandler(SyncMessageHandler):
    """
    Handle COMPUTE_QUEUE_ACQUIRED WebSocket message.

    Delegates to ComputeCapacityTelemetryHandler for aggregation.
    """

    def handle(self, data: dict[str, Any], ctx: HandlerContext) -> None:
        handler = ctx.get_capacity_telemetry_handler()
        if handler:
            handler.handle_queue_acquired(data)
