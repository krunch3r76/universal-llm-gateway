"""Shared inject budget constants — breaks body_injection ↔ inject_registry cycle.

Loaded from ``config/agents.yaml`` ``skill_delivery`` (typed repository config).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_AGENTS_YAML = Path(__file__).parent.parent.parent / "config" / "agents.yaml"

_DEFAULTS = {
    "body_budget_bytes": 50000,
    "handoff_inline_budget_bytes": 131072,
    "index_timeout_ms": 300,
    "body_timeout_ms": 300,
    "total_deadline_ms": 1500,
}


@lru_cache(maxsize=1)
def _skill_delivery() -> dict[str, int]:
    if not _AGENTS_YAML.is_file():
        return dict(_DEFAULTS)
    with _AGENTS_YAML.open() as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}
    block = raw.get("skill_delivery") or {}
    out = dict(_DEFAULTS)
    for key, default in _DEFAULTS.items():
        if key in block:
            out[key] = int(block[key])
        else:
            out[key] = default
    return out


def _budget(key: str) -> int:
    return _skill_delivery()[key]


INJECTED_BODY_BUDGET_BYTES = _budget("body_budget_bytes")
HANDOFF_INLINE_BUDGET_BYTES = _budget("handoff_inline_budget_bytes")
INJECTED_INDEX_TIMEOUT_MS = _budget("index_timeout_ms")
INJECTED_BODY_TIMEOUT_MS = _budget("body_timeout_ms")
INJECTED_TOTAL_DEADLINE_MS = _budget("total_deadline_ms")
