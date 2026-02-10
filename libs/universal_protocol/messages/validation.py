"""
Telemetry signal validation.

Enforces dot-notation naming specification for all telemetry messages.

Spec: services/universal-stargate/EVENTS.md
"""

import re
from typing import Final

# Pattern: domain.subdomain.action[.qualifier]
# - lowercase only
# - 2-5 segments
# - no underscores, no uppercase
TELEMETRY_SIGNAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z]+(\.[a-z]+){1,4}$"
)


def is_valid_telemetry_signal(signal: str) -> bool:
    """
    Check if telemetry signal follows dot-notation spec.

    Valid: telemetry.resource.updated, telemetry.model.loaded
    Invalid: resource_update, model_loaded

    Args:
        signal: Telemetry signal string to validate

    Returns:
        True if signal matches dot-notation pattern
    """
    return bool(TELEMETRY_SIGNAL_PATTERN.match(signal))


def validate_telemetry_signal(signal: str) -> None:
    """
    Validate telemetry signal, raising if invalid.

    Args:
        signal: Telemetry signal string to validate

    Raises:
        ValueError: If signal doesn't match dot-notation spec
    """
    if not is_valid_telemetry_signal(signal):
        raise ValueError(
            f"Invalid telemetry signal '{signal}'. "
            "Must be lowercase dot-notation (e.g., 'telemetry.resource.updated'). "
            "See EVENTS.md for specification."
        )
