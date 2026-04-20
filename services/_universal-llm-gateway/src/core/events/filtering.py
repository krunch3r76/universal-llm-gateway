"""
Event Filtering - Predicate-based event filtering for selective subscriptions.

Provides EventFilter class to enable conditional event routing based on:
- Event type
- Event attributes (e.g., model_id, error_message)
- Custom predicate functions
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class EventFilter:
    """
    Predicate-based event filter for conditional subscriptions.

    Enables selective event handling based on event attributes or custom logic.
    Filters can be combined with AND/OR logic.

    Examples:
        # Filter by model_id
        filter = EventFilter.by_attribute("model_id", "llama-3-8b")

        # Filter by error events only
        filter = EventFilter.by_predicate(lambda e: hasattr(e, 'error_message'))

        # Filter by multiple conditions (AND)
        filter = EventFilter.by_attribute("model_id", "llama-3-8b").and_predicate(
            lambda e: hasattr(e, 'vram_usage_mb') and e.vram_usage_mb > 8000
        )

        # Filter by multiple conditions (OR)
        filter = EventFilter.by_attribute("model_id", "llama-3-8b").or_attribute(
            "model_id", "mistral-7b"
        )
    """

    def __init__(self, predicate: Callable[[Any], bool]):
        """
        Initialize event filter with a predicate function.

        Args:
            predicate: Function that takes an event and returns True if it passes the filter
        """
        self.predicate = predicate

    def matches(self, event: Any) -> bool:
        """
        Check if event matches the filter.

        Args:
            event: Event to test against filter (UML Message structure with signal and payload)

        Returns:
            True if event passes the filter, False otherwise
        """
        try:
            return self.predicate(event)
        except Exception as e:
            # Try to get event identifier for logging
            event_id = getattr(event, "signal", type(event).__name__)
            logger.warning(f"Filter predicate failed for {event_id}: {e}")
            return False

    @classmethod
    def by_attribute(cls, attribute: str, value: Any) -> "EventFilter":
        """
        Create filter that matches events with specific payload attribute value.

        Works with UML Message structure: checks event.payload[attribute] == value

        Args:
            attribute: Payload attribute name to check
            value: Expected value for the attribute

        Returns:
            EventFilter that matches events where event.payload[attribute] == value
        """

        def predicate(event: Any) -> bool:
            if hasattr(event, "payload") and isinstance(event.payload, dict):
                return attribute in event.payload and event.payload[attribute] == value
            return False

        return cls(predicate)

    @classmethod
    def by_predicate(cls, predicate: Callable[[Any], bool]) -> "EventFilter":
        """
        Create filter with custom predicate function.

        Args:
            predicate: Custom function that takes event and returns True/False

        Returns:
            EventFilter with the custom predicate
        """
        return cls(predicate)

    @classmethod
    def by_type(cls, event_type: type) -> "EventFilter":
        """
        Create filter that matches events of specific type.

        Args:
            event_type: Event class to match

        Returns:
            EventFilter that matches events of the specified type
        """

        def predicate(event: Any) -> bool:
            return isinstance(event, event_type)

        return cls(predicate)

    @classmethod
    def by_attribute_contains(cls, attribute: str, substring: str) -> "EventFilter":
        """
        Create filter that matches events where payload attribute contains substring.

        Works with UML Message structure: checks if substring in event.payload[attribute]
        Useful for filtering by partial model_id or error_message.

        Args:
            attribute: Payload attribute name to check
            substring: Substring to search for

        Returns:
            EventFilter that matches events where attribute contains substring
        """

        def predicate(event: Any) -> bool:
            if not (hasattr(event, "payload") and isinstance(event.payload, dict)):
                return False
            if attribute not in event.payload:
                return False
            value = event.payload[attribute]
            return isinstance(value, str) and substring in value

        return cls(predicate)

    @classmethod
    def by_attribute_range(
        cls,
        attribute: str,
        min_value: Any | None = None,
        max_value: Any | None = None,
    ) -> "EventFilter":
        """
        Create filter that matches events where payload attribute is within range.

        Works with UML Message structure: checks event.payload[attribute] range.
        Useful for filtering by resource usage, duration, etc.

        Args:
            attribute: Payload attribute name to check
            min_value: Minimum value (inclusive), None for no minimum
            max_value: Maximum value (inclusive), None for no maximum

        Returns:
            EventFilter that matches events where min_value <= attribute <= max_value
        """

        def predicate(event: Any) -> bool:
            if not (hasattr(event, "payload") and isinstance(event.payload, dict)):
                return False
            if attribute not in event.payload:
                return False
            value = event.payload[attribute]

            if min_value is not None and value < min_value:
                return False
            if max_value is not None and value > max_value:
                return False
            return True

        return cls(predicate)

    def and_predicate(self, other_predicate: Callable[[Any], bool]) -> "EventFilter":
        """
        Combine this filter with another predicate using AND logic.

        Args:
            other_predicate: Another predicate function

        Returns:
            New EventFilter that requires both predicates to pass
        """

        def combined(event: Any) -> bool:
            return self.matches(event) and other_predicate(event)

        return EventFilter(combined)

    def and_filter(self, other_filter: "EventFilter") -> "EventFilter":
        """
        Combine this filter with another filter using AND logic.

        Args:
            other_filter: Another EventFilter

        Returns:
            New EventFilter that requires both filters to pass
        """
        return self.and_predicate(other_filter.predicate)

    def and_attribute(self, attribute: str, value: Any) -> "EventFilter":
        """
        Combine this filter with attribute check using AND logic.

        Args:
            attribute: Event attribute name
            value: Expected value

        Returns:
            New EventFilter with combined logic
        """
        return self.and_filter(EventFilter.by_attribute(attribute, value))

    def or_predicate(self, other_predicate: Callable[[Any], bool]) -> "EventFilter":
        """
        Combine this filter with another predicate using OR logic.

        Args:
            other_predicate: Another predicate function

        Returns:
            New EventFilter that passes if either predicate passes
        """

        def combined(event: Any) -> bool:
            return self.matches(event) or other_predicate(event)

        return EventFilter(combined)

    def or_filter(self, other_filter: "EventFilter") -> "EventFilter":
        """
        Combine this filter with another filter using OR logic.

        Args:
            other_filter: Another EventFilter

        Returns:
            New EventFilter that passes if either filter passes
        """
        return self.or_predicate(other_filter.predicate)

    def or_attribute(self, attribute: str, value: Any) -> "EventFilter":
        """
        Combine this filter with attribute check using OR logic.

        Args:
            attribute: Event attribute name
            value: Expected value

        Returns:
            New EventFilter with combined logic
        """
        return self.or_filter(EventFilter.by_attribute(attribute, value))

    def negate(self) -> "EventFilter":
        """
        Create filter with inverted logic (NOT).

        Returns:
            New EventFilter that passes when this filter fails
        """

        def inverted(event: Any) -> bool:
            return not self.matches(event)

        return EventFilter(inverted)


class FilteredEventBus:
    """
    Wrapper around EventBus that supports filtered subscriptions.

    Allows subscribers to register with filters so they only receive
    events that match their criteria. Works with UML Message structure.

    Example:
        filtered_bus = FilteredEventBus(event_bus)

        # Subscribe to only llama-3-8b model events by signal name
        filtered_bus.subscribe_filtered(
            "ModelLoaded",  # or MODEL_LOADED constant
            handle_llama_loaded,
            EventFilter.by_attribute("model_id", "llama-3-8b")
        )

        # Subscribe to high VRAM usage events
        filtered_bus.subscribe_filtered(
            "ModelLoaded",
            handle_high_vram,
            EventFilter.by_attribute_range("vram_usage_mb", min_value=10000)
        )
    """

    def __init__(self, event_bus):
        """
        Initialize filtered event bus wrapper.

        Args:
            event_bus: Underlying EventBus instance
        """
        self.event_bus = event_bus

    def subscribe_filtered(
        self, signal: str, handler: Callable[[Any], None], event_filter: EventFilter
    ):
        """
        Subscribe to events with filtering.

        Handler will only be called for events that pass the filter.

        Args:
            signal: Event signal name (string) to subscribe to
            handler: Handler function to call for matching events
            event_filter: EventFilter to apply before calling handler
        """

        def filtered_handler(event: Any):
            if event_filter.matches(event):
                try:
                    handler(event)
                except Exception as e:
                    logger.error(f"Error in filtered handler {handler.__name__}: {e}")

        self.event_bus.subscribe_async(signal, filtered_handler)
        logger.debug(
            f"Registered filtered subscription: {handler.__name__} for signal '{signal}'"
        )

    async def publish(self, event: Any):
        """
        Publish event asynchronously (delegates to underlying event bus).

        Args:
            event: Event to publish
        """
        await self.event_bus.publish(event)
