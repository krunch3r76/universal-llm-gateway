"""
Event-driven architecture support for Universal LLM Gateway.

This module provides:
- EventBus: Central event distribution system (from universal-event-bus)
- Event: UML Message structure for publishing events (from universal-event-bus)
- MinimalEventDebugBroadcaster: Debug logging for events (from universal-event-bus)
- LLM-specific event signals for model lifecycle and inference operations
- Event filtering: Predicate-based filtering for selective subscriptions

Usage (New UML Message Structure):
    from src.core.events import EventBus, Event, INFERENCE_STARTED, MODEL_LOADED

    # Initialize event bus
    event_bus = EventBus()

    # Subscribe to events by signal name
    def on_model_loaded(event: Event):
        print(f"Model {event.payload['model_id']} loaded using {event.payload['vram_usage_mb']}MB VRAM")
        print(f"Event ID: {event.id}, Timestamp: {event.timestamp}")

    event_bus.subscribe_async("ModelLoaded", on_model_loaded)
    # Or use constant:
    event_bus.subscribe_async(MODEL_LOADED, on_model_loaded)

    # Publish events using Event(signal, payload)
    # Use publish_async_nowait() for fire-and-forget (non-blocking)
    event_bus.publish_async_nowait(Event(
        signal="ModelLoaded",  # or MODEL_LOADED constant
        payload={
            "model_id": "llama-3-8b",
            "vram_usage_mb": 8192,
            "ram_usage_mb": 1024,
            "process_pid": 12345
        }
    ))
    # EventBus automatically adds:
    # - timestamp: ISO 8601 string
    # - id: global counter
    
    # Or use await publish_async() if you need confirmation:
    # await event_bus.publish_async(Event(...))  # Waits for delivery
"""

# Import core event infrastructure from universal-event-bus
from universal_event_bus import Event, EventBus, MinimalEventDebugBroadcaster

# Global event bus accessor (set during app initialization)
_global_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """
    Get the global event bus instance.

    Returns:
        EventBus: The global event bus

    Raises:
        RuntimeError: If event bus has not been initialized
    """
    if _global_event_bus is None:
        raise RuntimeError(
            "Event bus not initialized. Call set_event_bus() during app startup."
        )
    return _global_event_bus


def set_event_bus(event_bus: EventBus) -> None:
    """
    Set the global event bus instance.

    Should be called during app initialization.

    Args:
        event_bus: The EventBus instance to use globally
    """
    global _global_event_bus
    _global_event_bus = event_bus


# Import event aggregation
from .aggregation import EventAggregator, ModelStats, SystemStats

# Import crash event handlers
from .crash_handlers import (
    CentralizedCrashEventHandler,
    handle_health_check_failed,
    handle_socket_orphaned,
    handle_worker_crash,
)

# Import event filtering
from .filtering import EventFilter, FilteredEventBus

# Import event persistence
from .persistence import EventStore, EventStoreSubscriber
from .persistence_async import AsyncEventStore, AsyncEventStoreSubscriber
from .resource_monitoring import AsyncResourceMonitor, InferenceResourceSnapshot

# Import LLM-specific event signal constants
from .types import (
    # Configuration Event Signals
    CATALOG_RELOADED,
    # Gateway Lifecycle Event Signals
    GATEWAY_DRAINING,
    GATEWAY_SHUTDOWN,
    HEALTH_CHECK_FAILED,
    INFERENCE_COMPLETED,
    INFERENCE_FAILED,
    INFERENCE_RESOURCE_UPDATE,
    INFERENCE_STARTED,
    MODEL_LOAD_FAILED,
    MODEL_LOADED,
    # Model Lifecycle Event Signals
    MODEL_LOADING_STARTED,
    MODEL_UNLOADED,
    MODEL_UNLOADING_STARTED,
    # Inference Lifecycle Event Signals
    REQUEST_QUEUED,
    SOCKET_ORPHANED,
    STREAM_CANCELLATION_COMPLETE,
    STREAM_CANCELLED,
    # System Resource Event Signals
    SYSTEM_RESOURCES_UPDATED,
    # Worker Crash Detection Event Signals
    WORKER_CRASH_DETECTED,
)

__all__ = [
    # Core infrastructure
    "EventBus",
    "Event",
    "MinimalEventDebugBroadcaster",
    "get_event_bus",
    "set_event_bus",
    # Event filtering
    "EventFilter",
    "FilteredEventBus",
    # Event persistence
    "EventStore",
    "EventStoreSubscriber",
    "AsyncEventStore",
    "AsyncEventStoreSubscriber",
    "AsyncResourceMonitor",
    "InferenceResourceSnapshot",
    # Event aggregation
    "EventAggregator",
    "ModelStats",
    "SystemStats",
    # Crash event handlers
    "handle_worker_crash",
    "handle_socket_orphaned",
    "handle_health_check_failed",
    "CentralizedCrashEventHandler",
    # Signal Constants (UML Message Structure)
    "MODEL_LOADING_STARTED",
    "MODEL_LOADED",
    "MODEL_LOAD_FAILED",
    "MODEL_UNLOADING_STARTED",
    "MODEL_UNLOADED",
    "REQUEST_QUEUED",
    "INFERENCE_STARTED",
    "INFERENCE_COMPLETED",
    "INFERENCE_FAILED",
    "STREAM_CANCELLED",
    "STREAM_CANCELLATION_COMPLETE",
    "SYSTEM_RESOURCES_UPDATED",
    "INFERENCE_RESOURCE_UPDATE",
    "WORKER_CRASH_DETECTED",
    "SOCKET_ORPHANED",
    "HEALTH_CHECK_FAILED",
    # Gateway Lifecycle Event Signals
    "GATEWAY_DRAINING",
    "GATEWAY_SHUTDOWN",
    # Configuration Event Signals
    "CATALOG_RELOADED",
]
