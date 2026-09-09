"""D4 work-key scheme grammar shared by MCP intake and git_integration_worker.

MCP server runs with ``PYTHONPATH=/app/libs`` only — grammar must live here,
not under ``services/``.
"""

from __future__ import annotations

WORK_ITEM_SCHEMES: tuple[str, ...] = (
    "todo:",
    "plan:",
    "plan_phase:",
    "packet:",
    "agent-bus:",
    "friction:",
    "decision:",
)
ADHOC_SCHEME = "adhoc:"


def is_valid_work_key_scheme(work_key: str) -> bool:
    """D4 grammar — scheme-prefixed work identity."""
    key = work_key.strip()
    if key.startswith(ADHOC_SCHEME):
        return True
    return any(key.startswith(prefix) for prefix in WORK_ITEM_SCHEMES)


__all__ = ["ADHOC_SCHEME", "WORK_ITEM_SCHEMES", "is_valid_work_key_scheme"]
