"""
Universal Event Bus - Shared Event Infrastructure

This module provides shared event-driven architecture components
for universal-llm-gateway and universal-stargate.

Source: Originally developed in universal-stargate
Purpose: Shared event bus infrastructure for event-driven communication

Usage:
    from universal_event_bus.events import EventBus
    from universal_event_bus.events import MinimalEventDebugBroadcaster
    from universal_event_bus.transports import UDPTransport
    from universal_event_bus.bridges import UDPBridge
    from universal_event_bus.monitoring import MonitoringMessage
    from universal_event_bus.actor import CommandProcessor, Command, CommandResult

Features:
    - Event-driven publish/subscribe architecture
    - Synchronous and asynchronous event handlers
    - Optional debug broadcasting via Unix sockets
    - UDP transport for remote monitoring
    - Loose coupling with no shared state
    - Race condition elimination by design
    - Actor/CommandProcessor pattern for lock-free state management
"""

__version__ = "0.2.0"
__author__ = "Universal Event Bus Contributors"

# Re-export main components for convenience
from .actor import (
    Command,
    CommandProcessor,
    CommandResult,
    Sequential,
    SequentialExecutor,
    sequential,
)
from .bridges import UDPBridge
from .events import (
    EVENT_SIGNAL_PATTERN,
    Event,
    EventBus,
    MinimalEventDebugBroadcaster,
    Subscription,
    create_timestamp,
    event_factory,
    is_valid_event_signal,
    validate_event_signal,
)
from .monitoring import MonitoringMessage
from .transports import UDPTransport

__all__ = [
    # Events
    "Event",
    "EventBus",
    "MinimalEventDebugBroadcaster",
    "Subscription",
    "create_timestamp",
    "event_factory",
    "EVENT_SIGNAL_PATTERN",
    "is_valid_event_signal",
    "validate_event_signal",
    # Sequential execution (lock-free) - simple patterns
    "SequentialExecutor",
    "Sequential",
    "sequential",
    # Sequential execution - full command pattern
    "Command",
    "CommandProcessor",
    "CommandResult",
    # Transports
    "UDPTransport",
    "UDPBridge",
    "MonitoringMessage",
]
