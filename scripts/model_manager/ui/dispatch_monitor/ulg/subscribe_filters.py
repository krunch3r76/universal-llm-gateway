"""Shared v3 §11 subscribe filter definitions."""

from __future__ import annotations

LIVE_FILTERS: tuple[dict[str, str], ...] = (
    {"signal": "manage.charter.tick.*"},
    {"signal": "frontier.sdk.*"},
    {"signal": "cdp.generate.*"},
    {"signal": "frontier.poll.hint.issued"},
    {"signal": "mcp.agentbus.thread.cse.bound"},
    {"signal": "cdp.provenance.bound"},
    # Watermark orphan-clear for review-child ghosts after Stargate bounce (6164).
    {"signal": "system.started"},
)
