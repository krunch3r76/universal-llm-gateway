"""Shared event-driven infrastructure for universal-llm-gateway and universal-stargate.

Originally developed in universal-stargate. Provides event bus, transports,
monitoring, and actor patterns with no shared state and race-free design.

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
    ExceptionCaught,
    MinimalEventDebugBroadcaster,
    Subscription,
    capture_exception,
    create_timestamp,
    event_factory,
    is_valid_event_signal,
    validate_event_signal,
)
from .monitoring import MonitoringMessage
from .transports import UDPTransport

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = (
    'cloud_proxy',
    'gateway',
    'git_integration_worker',
    'mcp',
    'rag',
    'stargate',
)

__all__ = [
    # Events
    "Event",
    "EventBus",
    "ExceptionCaught",
    "capture_exception",
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
