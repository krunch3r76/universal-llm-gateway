"""
Message identification module for the universal transport message pump.

This module centralizes correlation ID extraction policy. It defines the default
strategy for locating correlation identifiers in inbound messages so that the
pump and its collaborators can route messages without duplicating heuristic logic.

The extractor recognizes three conventional field names (checked in order):
- correlation_id (snake_case, preferred in this codebase)
- correlationId (camelCase, common in JSON APIs)
- id (generic fallback used by some protocols)

Consumers should prefer injecting a custom get_correlation_id when their wire
format diverges from the above.
"""

from typing import Any


def default_get_correlation_id(message: dict[str, Any]) -> str | None:
    """
    Default correlation ID extractor used by MessagePump.

    Examines a message dictionary for a correlation identifier using a
    deterministic precedence order. This enables request/response matching
    and per-correlation streaming queues without requiring every message
    producer to use the same key.

    Lookup order (first non-None wins):
        1. message["correlation_id"]
        2. message["correlationId"]
        3. message["id"]

    Args:
        message: Arbitrary message dictionary received from a transport.
                 The function tolerates missing keys and non-string values
                 (they simply yield None for that candidate).

    Returns:
        The first matching correlation identifier as a string, or None if
        none of the conventional keys are present with a truthy value.
    """
    return (
        message.get("correlation_id")
        or message.get("correlationId")
        or message.get("id")
    )
