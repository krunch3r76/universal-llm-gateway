"""Trusted clocks for the concern effect ledger.

``SystemClock`` is the only wall-clock read in ``clock.py``, ``effects.py``,
``evidence.py``, and ``effect_gate.py``. Journal row timestamps are stamped
inside ``append_transition``, which slice 3b.1 does not edit.

Library functions keep a caller-supplied ``now``; only the test adapter takes
a ``TrustedClock``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class TrustedClock(Protocol):
    """Source of timezone-aware UTC instants."""

    def now(self) -> datetime:
        """Return the current instant as an aware UTC datetime."""


class SystemClock:
    """Wall clock. The only ``datetime.now`` call in this package outside tests."""

    def now(self) -> datetime:
        """Return ``datetime.now(tz=UTC)``."""
        return datetime.now(tz=UTC)


class FixedClock:
    """Test clock. ``advance`` mutates the private instant in place."""

    def __init__(self, at: datetime) -> None:
        self._at = require_aware_utc(at, name="at")

    def now(self) -> datetime:
        """Return the frozen instant."""
        return self._at

    def advance(self, delta: timedelta) -> None:
        """Move the frozen instant forward by *delta*."""
        self._at = self._at + delta


def require_aware_utc(value: datetime, *, name: str) -> datetime:
    """Return *value* as UTC, or raise ``ValueError`` when it is naive or not a datetime.

    Rejects values before any database work so a naive instant cannot start a write.
    """
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)
