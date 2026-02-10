"""
UDP bridge for EventBus.

Automatically forwards EventBus events to UDP transport for monitoring.
"""

from typing import Any

from universal_logging import get_logger

from ..monitoring.message_schemas import MonitoringMessage

logger = get_logger(__name__)


class UDPBridge:
    """
    Bridge between EventBus and UDP transport.

    Subscribes to EventBus events and forwards them to UDP transport
    for real-time monitoring.

    Features:
    - Automatic event forwarding
    - Optional event filtering
    - Custom event serialization
    - Standard message schema
    - Silent failure mode

    Example:
        from universal_event_bus import EventBus
        from universal_event_bus.transports import UDPTransport
        from universal_event_bus.bridges import UDPBridge

        event_bus = EventBus()
        udp_transport = UDPTransport(port=9999)
        udp_bridge = UDPBridge(event_bus, udp_transport)
        udp_bridge.start()

        # Now all events published to event_bus are forwarded to UDP
    """

    def __init__(
        self,
        event_bus: Any,
        udp_transport: Any,
        event_filter: set[str] | None = None,
        source: str | None = None,
    ):
        """
        Initialize UDP bridge.

        Args:
            event_bus: EventBus instance to monitor
            udp_transport: UDPTransport instance for sending events
            event_filter: Optional set of event signal names to forward
                (None = all events)
            source: Optional source identifier for messages
        """
        self.event_bus = event_bus
        self.udp_transport = udp_transport
        self.event_filter = event_filter
        self.source = source or "event_bus"
        self._started = False
        self._forwarded_count = 0
        self._error_count = 0

    def start(self):
        """
        Start bridge (enables event forwarding).

        Note: The actual subscription happens when EventBus publishes events
        if the bridge is set in EventBus constructor.
        """
        if self._started:
            logger.warning("UDP bridge already started")
            return

        self._started = True
        logger.info(f"UDP bridge started (port={self.udp_transport.port})")

    def stop(self):
        """Stop bridge (disables event forwarding)."""
        if not self._started:
            return

        self._started = False
        logger.info(
            f"UDP bridge stopped "
            f"(forwarded={self._forwarded_count}, errors={self._error_count})"
        )

    def forward_event(self, event: Any):
        """
        Forward Event to UDP transport.

        Args:
            event: Event instance with signal and payload
        """
        if not self._started:
            return

        # Import here to avoid circular dependency
        from ..events.event import Event

        # Type check
        if not isinstance(event, Event):
            logger.debug(f"Expected Event instance, got {type(event).__name__}")
            self._error_count += 1
            return

        # Check filter (by signal name)
        if self.event_filter is not None:
            if event.signal not in self.event_filter:
                return

        try:
            # Convert event to monitoring message
            message = MonitoringMessage.from_event(event, source=self.source)

            # Validate message
            if not message.validate():
                logger.debug(f"Invalid message generated from signal '{event.signal}'")
                self._error_count += 1
                return

            # Send via UDP transport
            message_dict = message.to_dict()
            self.udp_transport.send_message(message_dict)

            self._forwarded_count += 1

        except Exception as e:
            # Silent failure - monitoring should never break the application
            logger.debug(f"Failed to forward event: {e}")
            self._error_count += 1

    def set_event_filter(self, event_filter: set[str] | None):
        """
        Set event filter.

        Args:
            event_filter: Set of event signal names to forward (None = all events)
        """
        self.event_filter = event_filter
        count = len(event_filter) if event_filter else "all"
        logger.debug(f"Event filter updated: {count} signals")

    def add_signal(self, signal: str):
        """
        Add signal to filter.

        Args:
            signal: Event signal name to add
        """
        if self.event_filter is None:
            self.event_filter = set()
        self.event_filter.add(signal)
        logger.debug(f"Added signal '{signal}' to filter")

    def remove_signal(self, signal: str):
        """
        Remove signal from filter.

        Args:
            signal: Event signal name to remove
        """
        if self.event_filter and signal in self.event_filter:
            self.event_filter.remove(signal)
            logger.debug(f"Removed signal '{signal}' from filter")

    def clear_filter(self):
        """Clear event filter (forward all events)."""
        self.event_filter = None
        logger.debug("Event filter cleared (forwarding all events)")

    def get_stats(self) -> dict:
        """
        Get bridge statistics.

        Returns:
            Dictionary with statistics
        """
        return {
            "started": self._started,
            "forwarded_count": self._forwarded_count,
            "error_count": self._error_count,
            "filter_enabled": self.event_filter is not None,
            "filter_count": len(self.event_filter) if self.event_filter else 0,
            "source": self.source,
            "transport_queue_size": self.udp_transport.get_queue_size(),
        }

    def reset_stats(self):
        """Reset statistics counters."""
        self._forwarded_count = 0
        self._error_count = 0
        logger.debug("Bridge statistics reset")
