"""
Routing metrics consumer that emits routing metrics to UDP port.

This consumer listens for routing metric events (REQUEST_ROUTED,
MODEL_LOAD_INITIATED, MODEL_LOAD_COMPLETED, TOKEN_COUNT_COMPLETED)
and emits them to a UDP port where subscribers can listen.

Metrics are fire-and-forget - if no subscriber is listening, they're dropped
and don't consume memory or disk space.
"""

import json
import socket
import time

from universal_event_bus import Event, EventBus
from universal_logging import get_logger

from ..events import (
    MODEL_LOAD_COMPLETED,
    MODEL_LOAD_INITIATED,
    REQUEST_ROUTED,
    TOKEN_COUNT_COMPLETED,
)

logger = get_logger(__name__)


class RoutingMetricsConsumer:
    """
    Consumes routing metric events and emits them to UDP port.

    This consumer provides lightweight, fire-and-forget metrics emission.
    Metrics are sent to a UDP port where subscribers can listen if interested.
    If no subscriber is listening, metrics are dropped without consuming resources.

    Attributes:
        event_bus: EventBus instance for event subscription
        udp_host: UDP host to send metrics to
        udp_port: UDP port to send metrics to
        enabled: Whether metrics emission is enabled
    """

    def __init__(
        self,
        event_bus: EventBus,
        udp_host: str = "127.0.0.1",
        udp_port: int = 10001,
        enabled: bool = True,
    ):
        """
        Initialize routing metrics consumer.

        Args:
            event_bus: EventBus instance for event subscription
            udp_host: UDP host to send metrics to (default: localhost)
            udp_port: UDP port to send metrics to (default: 10001)
            enabled: Whether to enable metrics emission (default: True)
        """
        self.event_bus = event_bus
        self.udp_host = udp_host
        self.udp_port = udp_port
        self.enabled = enabled

        # Create UDP socket (connectionless - fire and forget)
        self.socket: socket.socket | None = None
        if self.enabled:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Set socket to non-blocking to avoid any delays
                self.socket.setblocking(False)
                logger.info(
                    f"✅ RoutingMetricsConsumer initialized (UDP {udp_host}:{udp_port})"
                )
            except Exception as e:
                logger.warning(f"⚠️ Failed to create UDP socket for metrics: {e}")
                self.socket = None
                self.enabled = False

    def start(self):
        """Start consuming routing metric events"""
        if not self.enabled:
            logger.info("RoutingMetricsConsumer disabled, not subscribing to events")
            return

        # Subscribe to routing metric events
        self.event_bus.subscribe_async(REQUEST_ROUTED, self._handle_request_routed)
        self.event_bus.subscribe_async(
            MODEL_LOAD_INITIATED, self._handle_model_load_initiated
        )
        self.event_bus.subscribe_async(
            MODEL_LOAD_COMPLETED, self._handle_model_load_completed
        )
        self.event_bus.subscribe_async(
            TOKEN_COUNT_COMPLETED, self._handle_token_count_completed
        )

        logger.info(
            "✅ RoutingMetricsConsumer started, subscribed to routing metric events"
        )

    def stop(self):
        """Stop consuming events and close UDP socket"""
        if self.socket:
            try:
                self.socket.close()
                logger.info("RoutingMetricsConsumer stopped, UDP socket closed")
            except Exception as e:
                logger.warning(f"Error closing UDP socket: {e}")

    async def _handle_request_routed(self, event: Event):
        """Handle REQUEST_ROUTED event"""
        await self._emit_metric("request_routed", event.payload)

    async def _handle_model_load_initiated(self, event: Event):
        """Handle MODEL_LOAD_INITIATED event"""
        await self._emit_metric("model_load_initiated", event.payload)

    async def _handle_model_load_completed(self, event: Event):
        """Handle MODEL_LOAD_COMPLETED event"""
        await self._emit_metric("model_load_completed", event.payload)

    async def _handle_token_count_completed(self, event: Event):
        """Handle TOKEN_COUNT_COMPLETED event"""
        await self._emit_metric("token_count_completed", event.payload)

    async def _emit_metric(self, metric_type: str, payload: dict):
        """
        Emit metric to UDP port (fire-and-forget).

        Args:
            metric_type: Type of metric (e.g., "request_routed")
            payload: Metric payload data
        """
        if not self.enabled or not self.socket:
            return

        try:
            # Add timestamp if not present
            if "timestamp" not in payload:
                payload["timestamp"] = time.time()

            # Create metric message
            metric_message = {"type": metric_type, "data": payload}

            # Serialize to JSON
            message_bytes = json.dumps(metric_message).encode("utf-8")

            # Send to UDP port (fire-and-forget - no error handling needed)
            # If no subscriber is listening, the message is dropped at OS level
            # This is exactly what we want - no memory consumption
            self.socket.sendto(message_bytes, (self.udp_host, self.udp_port))

        except Exception as e:
            # Silently ignore errors - metrics emission should never block or fail request processing
            # Only log at DEBUG level to avoid log spam
            logger.debug(f"Failed to emit metric {metric_type}: {e}")
