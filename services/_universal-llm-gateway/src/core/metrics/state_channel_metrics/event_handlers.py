"""Synchronous metric event handlers for state channel connection lifecycle events.

Implements connection, message, subscription, error, and summary handlers invoked
by the metrics collector queue processor after client validation checks pass.
"""

import time
from collections import deque
from typing import Any

from universal_logging import get_logger

from .channel_metrics import ChannelMetrics, track_operation

logger = get_logger(__name__)


class MetricEventHandlers:
    """Mixin with handler methods and shared metric mutation helpers."""

    _channels: dict[str, ChannelMetrics]
    _message_timestamps: deque
    _error_timestamps: deque
    total_connections: int
    total_disconnections: int
    total_messages: int
    total_errors: int

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
