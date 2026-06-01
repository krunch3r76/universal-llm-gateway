"""Metrics collection for state channel monitoring."""

import asyncio
import os
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChannelMetrics:
    """Metrics for a single state channel connection."""

    client_id: str
    connected_at: float = field(default_factory=time.time)
    disconnected_at: float | None = None

    # Message counts
    messages_sent: int = 0
    messages_received: int = 0
    updates_sent: int = 0
    deltas_sent: int = 0
    errors: int = 0

    # Data volume
    bytes_sent: int = 0
    bytes_received: int = 0

    # Subscriptions
    subscription_count: int = 0
    subscription_patterns: list[str] = field(default_factory=list)

    # Resource usage
    reservations_created: int = 0
    reservations_released: int = 0

    # Performance
    last_message_at: float = field(default_factory=time.time)
    avg_response_time_ms: float = 0.0

    def update_response_time(self, response_time_ms: float):
        """Update average response time with exponential moving average."""
        alpha = 0.1  # Smoothing factor
        self.avg_response_time_ms = (
            alpha * response_time_ms + (1 - alpha) * self.avg_response_time_ms
        )

    @property
    def duration_seconds(self) -> float:
        """Get connection duration in seconds."""
        end_time = self.disconnected_at or time.time()
        return end_time - self.connected_at

    @property
    def messages_per_second(self) -> float:
        """Calculate messages per second rate."""
        duration = self.duration_seconds
        if duration > 0:
            return (self.messages_sent + self.messages_received) / duration
        return 0.0


def track_operation(operation_name: str):
    """Decorator to track metric operations."""

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            start_time = time.time()
            try:
                result = func(self, *args, **kwargs)
                # Track success
                self._operation_count[operation_name] = (
                    self._operation_count.get(operation_name, 0) + 1
                )
                return result
            except Exception as e:
                # Track failure
                self._operation_errors[operation_name] = (
                    self._operation_errors.get(operation_name, 0) + 1
                )
                logger.warning(f"Operation {operation_name} failed: {e}")
                raise
            finally:
                # Track timing
                duration = time.time() - start_time
                if operation_name not in self._operation_timings:
                    self._operation_timings[operation_name] = deque(maxlen=1000)
                self._operation_timings[operation_name].append(duration)

        return wrapper

    return decorator


class StateChannelMetricsCollector:
    """Collects and aggregates metrics for all state channel connections."""

    def __init__(self):
        # Configuration via environment variables
        self._message_retention = int(os.getenv("METRICS_MESSAGE_RETENTION", "10000"))
        self._error_retention = int(os.getenv("METRICS_ERROR_RETENTION", "1000"))
        self._queue_size = int(os.getenv("METRICS_QUEUE_SIZE", "1000"))
        self._queue_timeout = float(os.getenv("METRICS_QUEUE_TIMEOUT", "0.1"))

        self._channels: dict[str, ChannelMetrics] = {}
        self.total_connections = 0
        self.total_disconnections = 0
        self.total_messages = 0
        self.total_errors = 0

        # Replace unbounded lists with bounded deques to prevent memory leaks
        self._message_timestamps: deque = deque(maxlen=self._message_retention)
        self._error_timestamps: deque = deque(maxlen=self._error_retention)

        # Operation tracking for decorators
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
        # Send shutdown signal
        await self._queue.put((None, (), {}, False, None))
        if self._processor_task:
            await self._processor_task
        self._processor_task = None
        self._queue = None
        self._result_queue = None
        logger.info("State channel metrics collector stopped")

    # Backward compatibility alias
    async def shutdown(self):
        """Stop background processor (alias for stop)."""
        await self.stop()

    async def _ensure_processor(self):
        loop = asyncio.get_running_loop()
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=self._queue_size)
        if self._result_queue is None:
            self._result_queue = asyncio.Queue()
        if self._processor_task is None or self._processor_task.done():
            self._processor_task = loop.create_task(self._process_queue())

    async def _submit(
        self, handler: Callable, *args, expects_result: bool = False, **kwargs
    ):
        """Submit metric operation with error isolation."""
        if not self._queue or not self._result_queue:
            logger.warning(
                f"Metrics queue not initialized, skipping {handler.__name__}"
            )
            return None

        try:
            await self._ensure_processor()
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            operation = (handler, args, kwargs, expects_result, future)

            await asyncio.wait_for(
                self._queue.put(operation), timeout=self._queue_timeout
            )

            if expects_result:
                return await asyncio.wait_for(future, timeout=self._queue_timeout * 2)
            return None

        except TimeoutError:
            logger.warning(f"Metrics queue operation timed out: {handler.__name__}")
            return None
        except Exception as e:
            logger.error(f"Metrics operation failed: {handler.__name__}: {e}")
            return None

    async def _process_queue(self):
        """Process queued metric operations with enhanced reliability."""
        logger.info("Metrics queue processor started")
        consecutive_errors = 0
        loop = asyncio.get_running_loop()
        last_cleanup = loop.time()

        while True:
            try:
                # Non-blocking approach: use asyncio.create_task to avoid blocking the event loop
                # Check for maintenance needs without blocking
                current_time = loop.time()
                if current_time - last_cleanup > 1.0:  # Maintenance every second
                    # Run cleanup in background without blocking
                    asyncio.create_task(self._periodic_cleanup())
                    last_cleanup = current_time

                # Use wait with a very short timeout to stay responsive
                try:
                    # This allows the event loop to process other tasks
                    queue_task = asyncio.create_task(self._queue.get())
                    done, pending = await asyncio.wait(
                        {queue_task},
                        timeout=0.001,  # 1ms - barely noticeable
                    )

                    if done:
                        handler, args, kwargs, expects_result, future = await queue_task
                        consecutive_errors = 0  # Reset on success
                    else:
                        # No item ready, cancel the get task and yield control
                        queue_task.cancel()
                        # Yield control to other tasks
                        await asyncio.sleep(0)
                        continue
                except asyncio.CancelledError:
                    # Task was cancelled, re-raise
                    raise

                # Check for shutdown signal
                if handler is None:
                    break

                # Process operation with error isolation
                try:
                    result = handler(*args, **kwargs)
                    if expects_result and not future.done():
                        future.set_result(result)
                except Exception as e:
                    logger.warning(f"Handler {handler.__name__} failed: {e}")
                    if expects_result and not future.done():
                        future.set_exception(e)
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                logger.info("Metrics queue processor cancelled")
                # Drain queue before exiting
                await self._drain_queue()
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Queue processor error #{consecutive_errors}: {e}")
                # Reset after too many consecutive errors
                max_consecutive_errors = 10
                if consecutive_errors > max_consecutive_errors:
                    logger.critical("Too many consecutive errors, resetting")
                    await asyncio.sleep(5)  # Back off
                    consecutive_errors = 0

    async def _periodic_cleanup(self):
        """Perform periodic maintenance tasks."""
        # Clean up old disconnected channels (older than 1 hour)
        await asyncio.sleep(0)  # Yield control
        self._handle_cleanup_old_channels(3600)

    async def _drain_queue(self):
        """Drain remaining queue operations on shutdown."""
        if not self._queue:
            return

        drained = 0
        while not self._queue.empty():
            try:
                operation = self._queue.get_nowait()
                handler, args, kwargs, expects_result, future = operation
                if future and not future.done():
                    future.set_exception(RuntimeError("Queue draining during shutdown"))
                self._queue.task_done()
                drained += 1
            except asyncio.QueueEmpty:
                break

        if drained > 0:
            logger.info(f"Drained {drained} operations from metrics queue")

    # Helper methods for common patterns
    def _validate_client(self, client_id: str) -> bool:
        """Validate client exists and is connected."""
        if client_id not in self._channels:
            logger.warning(f"Unknown client: {client_id}")
            return False

        channel = self._channels[client_id]
        if channel.disconnected_at is not None:
            logger.debug(f"Client already disconnected: {client_id}")
            return False

        return True

    def _record_timestamp(self, timestamp_list: deque, event_type: str):
        """Record timestamp for rate calculations."""
        timestamp_list.append(time.time())
        self.total_messages += 1
        logger.debug(f"Recorded {event_type} timestamp, total: {self.total_messages}")

    def _update_channel_metric(self, client_id: str, field: str, value: Any):
        """Safely update a channel metric field."""
        if client_id in self._channels:
            channel = self._channels[client_id]
            if hasattr(channel, field):
                current = getattr(channel, field)
                if isinstance(current, int | float):
                    setattr(channel, field, current + value)
                else:
                    setattr(channel, field, value)

    @track_operation("connection_management")
    def _handle_on_connection(
        self, client_id: str, auth_info: dict[str, Any] | None
    ) -> ChannelMetrics:
        self.total_connections += 1
        metrics = ChannelMetrics(client_id=client_id)
        self._channels[client_id] = metrics
        active_count = sum(
            1 for m in self._channels.values() if m.disconnected_at is None
        )
        logger.info(
            f"State channel connected: {client_id} (total active: {active_count})"
        )
        return metrics

    @track_operation("disconnection_management")
    def _handle_on_disconnection(self, client_id: str):
        if client_id in self._channels:
            metrics = self._channels[client_id]
            if metrics.disconnected_at is None:
                metrics.disconnected_at = time.time()
                self.total_disconnections += 1
                logger.info(
                    f"State channel disconnected: {client_id} "
                    f"(duration: {metrics.duration_seconds:.1f}s, "
                    f"messages: {metrics.messages_sent + metrics.messages_received}, "
                    f"errors: {metrics.errors})"
                )

    @track_operation("receive_message_stats")
    def _handle_on_message_received(self, client_id: str, message_size: int):
        if not self._validate_client(client_id):
            return

        self._record_timestamp(self._message_timestamps, "message_received")
        self._update_channel_metric(client_id, "messages_received", 1)
        self._update_channel_metric(client_id, "bytes_received", message_size)

        # Update last message timestamp
        channel = self._channels[client_id]
        channel.last_message_at = time.time()

    @track_operation("update_message_stats")
    def _handle_on_message_sent(
        self, client_id: str, message_type: str, message_size: int
    ):
        """Handle message sent event with automatic tracking."""
        if not self._validate_client(client_id):
            return

        self._record_timestamp(self._message_timestamps, "message_sent")
        self._update_channel_metric(client_id, "messages_sent", 1)
        self._update_channel_metric(client_id, "bytes_sent", message_size)

        # Update message type counters
        if message_type == "update":
            self._update_channel_metric(client_id, "updates_sent", 1)
        elif message_type == "delta":
            self._update_channel_metric(client_id, "deltas_sent", 1)

    def _handle_on_subscription(self, client_id: str, pattern: str):
        if not self._validate_client(client_id):
            return

        channel = self._channels[client_id]
        if pattern not in channel.subscription_patterns:
            channel.subscription_patterns.append(pattern)
            self._update_channel_metric(client_id, "subscription_count", 1)

    def _handle_on_error(self, client_id: str, error_type: str):
        # Record error even for disconnected clients
        if client_id in self._channels:
            self._update_channel_metric(client_id, "errors", 1)

        self.total_errors += 1
        self._error_timestamps.append(time.time())
        logger.warning(f"State channel error for {client_id}: {error_type}")

    def _handle_on_resource_action(self, client_id: str, action: str):
        if not self._validate_client(client_id):
            return

        if action == "reserve":
            self._update_channel_metric(client_id, "reservations_created", 1)
        elif action == "release":
            self._update_channel_metric(client_id, "reservations_released", 1)

    def _handle_get_active_connections(self) -> int:
        return sum(1 for m in self._channels.values() if m.disconnected_at is None)

    def _handle_get_metrics_summary(self) -> dict[str, Any]:
        current_time = time.time()
        cutoff = current_time - 300

        # No manual cleanup needed - deque automatically evicts old entries
        # Calculate rates based on recent messages (within last 5 minutes)
        recent_messages = [t for t in self._message_timestamps if t > cutoff]
        recent_errors = [t for t in self._error_timestamps if t > cutoff]

        message_rate = len(recent_messages) / 300.0 if recent_messages else 0
        error_rate = len(recent_errors) / 300.0 if recent_errors else 0

        active_channels = [
            {
                "client_id": m.client_id,
                "duration_seconds": m.duration_seconds,
                "messages_total": m.messages_sent + m.messages_received,
                "messages_per_second": m.messages_per_second,
                "subscriptions": m.subscription_count,
                "errors": m.errors,
            }
            for m in self._channels.values()
            if m.disconnected_at is None
        ]

        total_updates = sum(m.updates_sent for m in self._channels.values())
        total_deltas = sum(m.deltas_sent for m in self._channels.values())
        combined_updates = total_updates + total_deltas
        delta_ratio = total_deltas / combined_updates if combined_updates > 0 else 0

        return {
            "connections": {
                "active": self._handle_get_active_connections(),
                "total": self.total_connections,
                "disconnections": self.total_disconnections,
            },
            "messages": {
                "total": self.total_messages,
                "rate_per_second": message_rate,
                "errors": self.total_errors,
                "error_rate_per_second": error_rate,
            },
            "efficiency": {
                "delta_ratio": delta_ratio,
                "total_updates": total_updates,
                "total_deltas": total_deltas,
            },
            "active_channels": active_channels,
            "bandwidth": {
                "bytes_sent": sum(m.bytes_sent for m in self._channels.values()),
                "bytes_received": sum(
                    m.bytes_received for m in self._channels.values()
                ),
            },
        }

    def _handle_cleanup_old_channels(self, max_age_seconds: int) -> dict[str, int]:
        current_time = time.time()
        to_remove = [
            client_id
            for client_id, metrics in self._channels.items()
            if metrics.disconnected_at
            and (current_time - metrics.disconnected_at) > max_age_seconds
        ]
        for client_id in to_remove:
            del self._channels[client_id]
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old channel metrics")
        return {"removed": len(to_remove), "remaining": len(self._channels)}


# Global metrics collector instance
state_channel_metrics = StateChannelMetricsCollector()
