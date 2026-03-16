"""Event factory decorator for enforcing factory function pattern.

Provides @event_factory decorator that allows Event construction within
factory functions while preventing direct Event() construction elsewhere.

Validates at creation time: signal format (dot-notation), role (one of
'coordination', 'observation', 'debug'), and scope (one of 'node', 'global').
"""

import threading
from collections.abc import Callable
from functools import wraps
from typing import Any, cast

from .event import Event
from .validation import validate_event_signal

# Thread-local flag for Event construction authorization
_allow_construction = threading.local()

_VALID_ROLES = frozenset({"coordination", "observation", "debug"})
_VALID_SCOPES = frozenset({"node", "global"})


def event_factory[F: Callable[..., Event]](func: F) -> F:
    """
    Decorator for Event factory functions.

    Automatically manages thread-local construction flag to allow
    Event() construction within the decorated function.

    Also validates signal format (dot-notation), role, and scope at call time.

    Example:
        from universal_event_bus import Event, event_factory

        @event_factory
        def ModelExecutionCompleted(model_id: str) -> Event:
            '''Create model.execution.completed event.'''
            return Event(
                signal="model.execution.completed",
                payload={"model_id": model_id},
                role="coordination",
            )

    Args:
        func: Factory function that returns an Event

    Returns:
        Wrapped function with automatic flag management

    Raises:
        TypeError: If decorated function doesn't return Event instance
        ValueError: If event signal, role, or scope is invalid
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Event:
        _allow_construction.value = True
        try:
            event = func(*args, **kwargs)
            # Verify factory actually returns an Event
            if not isinstance(event, Event):
                raise TypeError(
                    f"Factory function {func.__name__} must return Event instance, "
                    f"got {type(event).__name__}"
                )

            # Validate signal format (dot-notation)
            validate_event_signal(event.signal)

            if event.role not in _VALID_ROLES:
                raise ValueError(
                    f"Invalid event role '{event.role}' for signal '{event.signal}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_ROLES))}"
                )
            if event.scope not in _VALID_SCOPES:
                raise ValueError(
                    f"Invalid event scope '{event.scope}' for signal '{event.signal}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_SCOPES))}"
                )

            return event
        finally:
            _allow_construction.value = False

    return cast(F, wrapper)
