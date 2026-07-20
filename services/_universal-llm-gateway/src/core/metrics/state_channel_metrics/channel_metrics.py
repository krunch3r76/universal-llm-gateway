"""Per-connection metric record and operation-tracking decorator for state channels.

Defines ChannelMetrics dataclass fields and the track_operation decorator used
by the metrics collector to count successes, failures, and latencies per handler.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from functools import wraps

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChannelMetrics:
    """Metrics for a single state channel connection."""

    client_id: str
    connected_at: float = field(default_factory=time.time)
    disconnected_at: float | None = None

    messages_sent: int = 0
    messages_received: int = 0
    updates_sent: int = 0
    deltas_sent: int = 0
    errors: int = 0

    bytes_sent: int = 0
    bytes_received: int = 0

    subscription_count: int = 0
    subscription_patterns: list[str] = field(default_factory=list)

    reservations_created: int = 0
    reservations_released: int = 0

    last_message_at: float = field(default_factory=time.time)
    avg_response_time_ms: float = 0.0

    def update_response_time(self, response_time_ms: float):
        """Update average response time with exponential moving average."""
        alpha = 0.1
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
    """Decorator to track metric operations on StateChannelMetricsCollector instances."""

    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            start_time = time.time()
            try:
                result = func(self, *args, **kwargs)
                self._operation_count[operation_name] = (
                    self._operation_count.get(operation_name, 0) + 1
                )
                return result
            except Exception as e:
                self._operation_errors[operation_name] = (
                    self._operation_errors.get(operation_name, 0) + 1
                )
                logger.warning(f"Operation {operation_name} failed: {e}")
                raise
            finally:
                duration = time.time() - start_time
                if operation_name not in self._operation_timings:
                    self._operation_timings[operation_name] = deque(maxlen=1000)
                self._operation_timings[operation_name].append(duration)

        return wrapper

    return decorator
