"""
Routing decision consumer that aggregates decision trace metrics.

Subscribes to ROUTING_DECISION and ROUTING_DECISION_FAILED events
and provides aggregated metrics for monitoring and observability.

Separate from RoutingMetricsConsumer which emits to UDP.
"""

import time
from collections import defaultdict
from typing import Any

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import ROUTING_DECISION, ROUTING_DECISION_FAILED

logger = get_logger(__name__)


class RoutingDecisionConsumer:
    """
    Consume ROUTING_DECISION events and aggregate metrics.

    Tracks:
    - Routing success rate by model
    - Average evaluation time
    - Tier distribution (T1/T2/T0)
    - Gateway selection frequency
    """

    def __init__(
        self,
        event_bus: EventBus,
        report_interval_sec: float = 60.0,
    ):
        """
        Initialize routing decision consumer.

        Args:
            event_bus: EventBus instance for event subscription
            report_interval_sec: Interval for periodic metric reporting
        """
        self._event_bus = event_bus
        self._report_interval_sec = report_interval_sec

        # Metrics storage
        self._metrics: dict[str, Any] = {
            "total_decisions": 0,
            "successful_decisions": 0,
            "failed_decisions": 0,
            "by_tier": defaultdict(int),
            "by_gateway": defaultdict(int),
            "by_model": defaultdict(
                lambda: {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "avg_eval_time_ms": 0.0,
                }
            ),
            "total_eval_time_ms": 0.0,
        }
        self._last_report_time = time.time()
        self._started = False

    def start(self) -> None:
        """Start consuming routing decision events."""
        if self._started:
            return

        # Subscribe to routing decision events (synchronous subscription)
        self._event_bus.subscribe_async(ROUTING_DECISION, self._handle_routing_decision)
        self._event_bus.subscribe_async(
            ROUTING_DECISION_FAILED, self._handle_routing_decision
        )

        self._started = True
        logger.info("✅ RoutingDecisionConsumer started (decision trace aggregation)")

    def stop(self) -> None:
        """Stop consuming events."""
        # Note: EventBus handlers persist for bus lifetime (no unsubscribe support)
        self._started = False
        logger.info("RoutingDecisionConsumer stopped")

    async def _handle_routing_decision(self, event: Event) -> None:
        """Handle routing decision event and update metrics."""
        try:
            payload = event.payload

            model_id = payload.get("model_id")
            selected_gateway = payload.get("selected_gateway")
            selection_tier = payload.get("selection_tier")
            eval_time_ms = payload.get("evaluation_time_ms", 0.0)

            # Update total metrics
            self._metrics["total_decisions"] += 1
            self._metrics["total_eval_time_ms"] += eval_time_ms

            if selected_gateway:
                self._metrics["successful_decisions"] += 1
                self._metrics["by_gateway"][selected_gateway] += 1
            else:
                self._metrics["failed_decisions"] += 1

            if selection_tier:
                self._metrics["by_tier"][selection_tier] += 1

            # Update per-model metrics
            if model_id:
                model_metrics = self._metrics["by_model"][model_id]
                model_metrics["total"] += 1
                if selected_gateway:
                    model_metrics["successful"] += 1
                else:
                    model_metrics["failed"] += 1

                # Update rolling average eval time
                current_avg = model_metrics["avg_eval_time_ms"]
                total = model_metrics["total"]
                model_metrics["avg_eval_time_ms"] = (
                    current_avg * (total - 1) + eval_time_ms
                ) / total

            # Periodic reporting
            current_time = time.time()
            if current_time - self._last_report_time >= self._report_interval_sec:
                self._report_metrics()
                self._last_report_time = current_time

        except Exception as e:
            logger.warning(f"Error handling routing decision event: {e}")

    def _report_metrics(self) -> None:
        """Report aggregated metrics to log."""
        total = self._metrics["total_decisions"]
        if total == 0:
            return

        success_rate = self._metrics["successful_decisions"] / total * 100
        avg_eval_time = self._metrics["total_eval_time_ms"] / total

        logger.info(
            f"📊 Routing Decision Metrics (last {self._report_interval_sec}s): "
            f"total={total}, success_rate={success_rate:.1f}%, "
            f"avg_eval_time={avg_eval_time:.2f}ms"
        )

        # Log tier distribution
        tier_dist = dict(self._metrics["by_tier"])
        if tier_dist:
            logger.info(f"   Tier distribution: {tier_dist}")

        # Log top gateways
        top_gateways = sorted(
            self._metrics["by_gateway"].items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]
        if top_gateways:
            logger.info(f"   Top gateways: {dict(top_gateways)}")

    def get_metrics(self) -> dict[str, Any]:
        """
        Get current metrics snapshot.

        Returns:
            Dict with all aggregated metrics
        """
        total = self._metrics["total_decisions"]

        return {
            "total_decisions": total,
            "successful_decisions": self._metrics["successful_decisions"],
            "failed_decisions": self._metrics["failed_decisions"],
            "success_rate": (
                self._metrics["successful_decisions"] / total * 100 if total > 0 else 0
            ),
            "avg_eval_time_ms": (
                self._metrics["total_eval_time_ms"] / total if total > 0 else 0
            ),
            "by_tier": dict(self._metrics["by_tier"]),
            "by_gateway": dict(self._metrics["by_gateway"]),
            "by_model": {k: dict(v) for k, v in self._metrics["by_model"].items()},
        }

    def reset_metrics(self) -> None:
        """Reset all metrics to zero."""
        self._metrics = {
            "total_decisions": 0,
            "successful_decisions": 0,
            "failed_decisions": 0,
            "by_tier": defaultdict(int),
            "by_gateway": defaultdict(int),
            "by_model": defaultdict(
                lambda: {
                    "total": 0,
                    "successful": 0,
                    "failed": 0,
                    "avg_eval_time_ms": 0.0,
                }
            ),
            "total_eval_time_ms": 0.0,
        }
        self._last_report_time = time.time()
        logger.info("RoutingDecisionConsumer metrics reset")
