"""
DEPRECATED: This module is no longer needed.

Debug broadcasting is now integrated directly into the main EventBus.
Use EventBus(debug_broadcaster=debug_broadcaster) instead.

Example:
    from universal_event_bus.events import EventBus, MinimalEventDebugBroadcaster

    debug_broadcaster = MinimalEventDebugBroadcaster("/tmp/stargate_debug_events.sock")
    await debug_broadcaster.start_debug_server()
    event_bus = EventBus(debug_broadcaster)
"""

import warnings

warnings.warn(
    "DebuggableEventBus is deprecated. Use"
    "EventBus(debug_broadcaster=debug_broadcaster) instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Import from universal-event-bus module
from universal_event_bus.events import EventBus  # noqa: E402

# For backward compatibility, alias EventBus as DebuggableEventBus
DebuggableEventBus = EventBus
