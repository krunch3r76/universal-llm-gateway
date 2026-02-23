"""
Model execution lifecycle tracker.

Subscribes to model.execution.started, model.execution.completed, and
model.execution.failed events to track which models have active execution
requests on each gateway.

Current implementation: Set-based (1 request at a time, llama.cpp)
Future: Counter-based (N concurrent requests, vLLM batching)
"""

from collections import defaultdict

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import (
    MODEL_EXECUTION_COMPLETED,
    MODEL_EXECUTION_FAILED,
    MODEL_EXECUTION_STARTED,
)

logger = get_logger(__name__)


class ModelExecutionTracker:
    """
    Track model execution lifecycle across gateways.

    Aggregates execution started/completed/failed events to determine which
    models are currently processing requests.

    Current: Set-based tracking (binary busy state)
    Future: Counter-based tracking for vLLM batching support
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._active_executions: dict[str, set[str]] = defaultdict(set)

    def start(self) -> None:
        """Start tracking execution lifecycle events."""
        self.event_bus.subscribe_async(
            MODEL_EXECUTION_STARTED, self._handle_execution_started
        )
        self.event_bus.subscribe_async(
            MODEL_EXECUTION_COMPLETED, self._handle_execution_completed
        )
        self.event_bus.subscribe_async(
            MODEL_EXECUTION_FAILED, self._handle_execution_completed
        )
        logger.info("✅ ModelExecutionTracker started")

    def stop(self) -> None:
        """Stop tracking events."""
        logger.info("ModelExecutionTracker stopped")

    async def _handle_execution_started(self, event: Event) -> None:
        """Track execution request starting on model."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")

        if not gateway_url or not model_id:
            return

        self._active_executions[gateway_url].add(model_id)
        logger.debug(f"🔒 Execution started: {gateway_url} / {model_id}")

    async def _handle_execution_completed(self, event: Event) -> None:
        """Track execution request completing on model."""
        payload = event.payload
        gateway_url = payload.get("url")
        model_id = payload.get("model_id")

        if not gateway_url:
            return

        if model_id:
            self._active_executions[gateway_url].discard(model_id)
        else:
            self._active_executions[gateway_url].clear()

        logger.debug(f"🔓 Execution completed: {gateway_url} / {model_id}")

    def is_model_busy(self, gateway_url: str, model_id: str | None = None) -> bool:
        """Check if model has active execution (optionally for specific model)."""
        busy = self._active_executions.get(gateway_url, set())
        if model_id:
            return model_id in busy
        return len(busy) > 0

    def get_busy_models(self, gateway_url: str) -> set[str]:
        """Get models with active executions on gateway."""
        return self._active_executions.get(gateway_url, set()).copy()
