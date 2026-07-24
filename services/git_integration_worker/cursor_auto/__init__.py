"""Cursor Auto orchestrator for ``lane:cursor-auto`` agent_bus.request jobs.

v0: admit-on-request enqueue + orphan scanner + wire-map resolution +
nested ``cursor_sdk_gate`` serialize. Distinct from SDK ``cursor/auto`` and
from ``lane:life-to-code``.
"""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.liveness import (
    AutoLivenessRegistry,
    get_registry,
)
from services.git_integration_worker.cursor_auto.wire_map import (
    resolve_contract_disposition,
    resolve_desired_effort,
    resolve_desired_model,
)

__all__ = [
    "AutoLivenessRegistry",
    "get_registry",
    "resolve_contract_disposition",
    "resolve_desired_effort",
    "resolve_desired_model",
]
