"""
Patch workflow definitions.

Each workflow module defines patches for a specific vLLM version range.
Workflows are registered via register_all_workflows().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..registry import PatchRegistry


def register_all_workflows(registry: PatchRegistry) -> None:
    """Register all patch workflows with the registry."""
    from . import v0_13, v0_14

    v0_13.register(registry)
    v0_14.register(registry)
