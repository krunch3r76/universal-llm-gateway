"""
Universal Event Bus - Shared Event Infrastructure

This package provides shared event-driven architecture components
for universal-llm-gateway and universal-stargate.

Components:
    - Event: UML Message-based event structure (signal + payload)
    - EventBus: Core event bus for publish/subscribe patterns
    - MinimalEventDebugBroadcaster: Debug event monitoring via Unix sockets
    - event_factory: Decorator for creating Event factory functions
"""

from .debug_broadcaster import (
    DebugClient,
    MinimalEventDebugBroadcaster,
    SimpleTransportWrapper,
)
from .event import Event, create_timestamp
from .event_bus import EventBus, Subscription
from .factory import event_factory
from .validation import (
    EVENT_SIGNAL_PATTERN,
    is_valid_event_signal,
    validate_event_signal,
)

__all__ = [
    "Event",
    "create_timestamp",
    "EventBus",
    "Subscription",
    "event_factory",
    "MinimalEventDebugBroadcaster",
    "DebugClient",
    "SimpleTransportWrapper",
    "EVENT_SIGNAL_PATTERN",
    "is_valid_event_signal",
    "validate_event_signal",
]
