"""State channel metrics collector public API and process lifecycle management.

Composes queue processing and event handler mixins into StateChannelMetricsCollector
and exposes the module-level singleton consumed by WebSocket and metrics routes.
"""

import asyncio
import os
from collections import deque
from typing import Any

from universal_logging import get_logger

from .channel_metrics import ChannelMetrics
from .event_handlers import MetricEventHandlers
from .queue_processor import MetricQueueProcessor

logger = get_logger(__name__)


class StateChannelMetricsCollector(MetricQueueProcessor, MetricEventHandlers):
    """Collects and aggregates metrics for all state channel connections."""

    def __init__(self):
        self._message_retention = int(os.getenv("METRICS_MESSAGE_RETENTION", "10000"))
        self._error_retention = int(os.getenv("METRICS_ERROR_RETENTION", "1000"))
        self._queue_size = int(os.getenv("METRICS_QUEUE_SIZE", "1000"))
        self._queue_timeout = float(os.getenv("METRICS_QUEUE_TIMEOUT", "0.1"))

        self._channels: dict[str, ChannelMetrics] = {}
        self.total_connections = 0
        self.total_disconnections = 0
        self.total_messages = 0
        self.total_errors = 0

        self._message_timestamps: deque = deque(maxlen=self._message_retention)
        self._error_timestamps: deque = deque(maxlen=self._error_retention)

        self._operation_count: dict[str, int] = {}
        self._operation_errors: dict[str, int] = {}
        self._operation_timings: dict[str, deque] = {}

        self._queue: asyncio.Queue | None = None
        self._result_queue: asyncio.Queue | None = None
        self._processor_task: asyncio.Task | None = None

    async def on_connection(
        self, client_id: str, auth_info: dict[str, Any] | None = None
    ) -> ChannelMetrics:
        """Record a new connection."""
        return await self._submit(
            self._handle_on_connection, client_id, auth_info, expects_result=True
        )

    async def on_disconnection(self, client_id: str):
        """Record a disconnection."""
        await self._submit(self._handle_on_disconnection, client_id)

    async def on_message_received(self, client_id: str, message_size: int):
        """Record an incoming message."""
        await self._submit(self._handle_on_message_received, client_id, message_size)

    async def on_message_sent(
        self, client_id: str, message_type: str, message_size: int
    ):
        """Record an outgoing message."""
        await self._submit(
            self._handle_on_message_sent, client_id, message_type, message_size
        )

    async def on_subscription(self, client_id: str, pattern: str):
        """Record a subscription."""
        await self._submit(self._handle_on_subscription, client_id, pattern)

    async def on_error(self, client_id: str, error_type: str):
        """Record an error."""
        await self._submit(self._handle_on_error, client_id, error_type)

    async def on_resource_action(self, client_id: str, action: str):
        """Record a resource action."""
        await self._submit(self._handle_on_resource_action, client_id, action)

    async def get_active_connections(self) -> int:
        """Get number of active connections."""
        return await self._submit(
            self._handle_get_active_connections, expects_result=True
        )

    async def get_metrics_summary(self) -> dict[str, Any]:
        """Get summary of all metrics."""
        return await self._submit(self._handle_get_metrics_summary, expects_result=True)

    async def cleanup_old_channels(self, max_age_seconds: int = 3600) -> dict[str, int]:
        """Clean up old disconnected channels."""
        return await self._submit(
            self._handle_cleanup_old_channels, max_age_seconds, expects_result=True
        )

    async def start(self):
        """Start the metrics collector."""
        await self._ensure_processor()
        logger.info("State channel metrics collector started")

    async def stop(self):
        """Stop background processor."""
        if self._queue is None:
            return
        await self._queue.put((None, (), {}, False, None))
        if self._processor_task:
            await self._processor_task
        self._processor_task = None
        self._queue = None
        self._result_queue = None
        logger.info("State channel metrics collector stopped")

    async def shutdown(self):
        """Stop background processor (alias for stop)."""
        await self.stop()


state_channel_metrics = StateChannelMetricsCollector()
