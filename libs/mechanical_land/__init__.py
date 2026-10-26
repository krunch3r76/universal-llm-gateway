"""Mechanical-land journal library — schema and pure fold (stage 1)."""

from mechanical_land.fold import fold, fresh_state
from mechanical_land.schema import (
    JOURNAL_EVENT_NAMES,
    JournalEntry,
    JournalForkRefused,
    RunState,
    UnknownJournalEvent,
)

CONSUMERS: tuple[str, ...] = ("git_integration_worker",)

__all__ = [
    "JOURNAL_EVENT_NAMES",
    "JournalEntry",
    "JournalForkRefused",
    "RunState",
    "UnknownJournalEvent",
    "fold",
    "fresh_state",
]
