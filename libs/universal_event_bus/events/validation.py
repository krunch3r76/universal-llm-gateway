"""
Event signal validation.

Enforces dot-notation naming specification for all events.

Spec: services/universal-stargate/EVENTS.md
"""

import re
from typing import Final

# Pattern: domain.subdomain.action[.qualifier]
# - lowercase only
# - 2-5 segments
# - no underscores, no uppercase
EVENT_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]+(\.[a-z]+){1,4}$")


def is_valid_event_signal(signal: str) -> bool:
    """
    Check if event signal follows dot-notation spec.

    Valid: model.execution.completed, gateway.state.changed
    Invalid: ModelExecutionCompleted, MODEL_EXECUTION_COMPLETED

    Args:
        signal: Event signal string to validate

    Returns:
        True if signal matches dot-notation pattern
    """
    return bool(EVENT_SIGNAL_PATTERN.match(signal))


def validate_event_signal(signal: str) -> None:
    """
    Validate event signal, raising if invalid.

    Args:
        signal: Event signal string to validate

    Raises:
        ValueError: If signal doesn't match dot-notation spec
    """
    if not is_valid_event_signal(signal):
        raise ValueError(
            f"Invalid event signal '{signal}'. "
            f"Must be lowercase dot-notation (e.g., 'model.execution.completed'). "
            f"See EVENTS.md for specification."
        )
