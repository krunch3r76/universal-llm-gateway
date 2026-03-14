"""
Universal Event Bus - Shared Event Infrastructure

This package provides shared event-driven architecture components
for universal-llm-gateway and universal-stargate.

Components:
    - Event: UML Message-based event structure (signal + payload)
    - EventBus: Core event bus for publish/subscribe patterns
    - MinimalEventDebugBroadcaster: Debug event monitoring via Unix sockets
    - DebugClient: Client for connecting to the debug broadcaster
    - SimpleTransportWrapper: Wrapper for debug transport
    - event_factory: Decorator for creating Event factory functions
    - ExceptionCaught: Event factory for structured exception telemetry
    - capture_exception: Async context manager that emits ExceptionCaught on Exception
"""

from .debug_broadcaster import (
    DebugClient,
    MinimalEventDebugBroadcaster,
    SimpleTransportWrapper,
)
from .event import Event, create_timestamp
from .event_bus import EventBus, Subscription
from .exception_events import ExceptionCaught, capture_exception
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
    "ExceptionCaught",
    "capture_exception",
    "MinimalEventDebugBroadcaster",
    "DebugClient",
    "SimpleTransportWrapper",
    "EVENT_SIGNAL_PATTERN",
    "is_valid_event_signal",
    "validate_event_signal",
]
