"""Canonical effort vocabulary — ladder, aliases, per-surface renderers.

Capability (what a given model accepts) stays in ``cursor_capabilities``.
This module owns only vocabulary: normalize aliases, render to wire / picker /
testid. Surfaces must not restate alias literals.
"""

from __future__ import annotations

from effort_vocabulary.core import (
    EFFORT_TOKENS,
    PICKER_LADDER,
    PROVIDER_EXTENDED,
    WIRE_LADDER,
    normalize_effort,
    to_picker_suffix,
    to_testid,
    to_wire,
)

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'stargate')

__all__ = [
    "EFFORT_TOKENS",
    "PICKER_LADDER",
    "PROVIDER_EXTENDED",
    "WIRE_LADDER",
    "normalize_effort",
    "to_picker_suffix",
    "to_testid",
    "to_wire",
]
