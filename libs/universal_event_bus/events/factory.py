"""
Event factory decorator for enforcing factory function pattern.

Provides @event_factory decorator that allows Event construction within
factory functions while preventing direct Event() construction elsewhere.

Also validates event signal format (dot-notation) at creation time.
"""

import threading
from collections.abc import Callable
from functools import wraps
from typing import Any

from .validation import validate_event_signal

# Thread-local flag for Event construction authorization
_allow_construction = threading.local()


def event_factory[F: Callable[..., Any]](func: F) -> F:
    """
    Decorator for Event factory functions.

    Automatically manages thread-local construction flag to allow
    Event() construction within the decorated function.

    Also validates that the event signal follows dot-notation spec.

    Example:
        from universal_event_bus import Event, event_factory

        @event_factory
        def ModelExecutionCompleted(model_id: str) -> Event:
            '''Create model.execution.completed event.'''
            return Event(
                signal="model.execution.completed",
                payload={"model_id": model_id},
            )

    Args:
        func: Factory function that returns an Event

    Returns:
        Wrapped function with automatic flag management

    Raises:
        TypeError: If decorated function doesn't return Event instance
        ValueError: If event signal doesn't match dot-notation spec
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        _allow_construction.value = True
        try:
            event = func(*args, **kwargs)
            # Verify factory actually returns an Event
            from .event import Event

            if not isinstance(event, Event):
                raise TypeError(
                    f"Factory function {func.__name__} must return Event instance, "
                    f"got {type(event).__name__}"
                )

            # Validate signal format (dot-notation)
            validate_event_signal(event.signal)

            return event
        finally:
            _allow_construction.value = False

    return wrapper  # type: ignore
