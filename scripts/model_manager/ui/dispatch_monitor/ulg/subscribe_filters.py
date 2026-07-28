"""Shared v3 §11 subscribe filter definitions."""

from __future__ import annotations

LIVE_FILTERS: tuple[dict[str, str], ...] = (
    {"signal": "manage.charter.tick.*"},
    {"signal": "manage.charter.conveyor.*"},
    {"signal": "frontier.sdk.*"},
    {"signal": "cdp.generate.*"},
    {"signal": "frontier.poll.hint.issued"},
)
