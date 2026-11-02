"""Operational receipt grammars over agent-bus turn bodies.

MCP ``agent_bus.hop`` imports :mod:`receipts.consumption` for unconsumed ruling
markers on continuity handoffs.
"""

from __future__ import annotations

from receipts.consumption import (
    CONSUMED_TYPE_RE,
    CONSUMPTION_MISSING_PREFIX,
    RULING_TYPE_RES,
    consumption_missing_marker,
    find_unconsumed_rulings,
    parse_consumed,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("mcp",)

__all__ = [
    "CONSUMERS",
    "CONSUMED_TYPE_RE",
    "CONSUMPTION_MISSING_PREFIX",
    "RULING_TYPE_RES",
    "consumption_missing_marker",
    "find_unconsumed_rulings",
    "parse_consumed",
]
