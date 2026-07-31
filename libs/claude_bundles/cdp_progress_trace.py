"""Progress-fingerprint trace for CDP generate poll loops.

A CDP leg that exhausts ``max_wall_s`` is aborted by us, not by the satellite,
so ``wall_clock_exceeded`` on its own cannot say whether the session was a long
task we cut off or a dead session still returning well-formed status. The poll
loop already computes a progress fingerprint on every snapshot; this module
retains a bounded history of the points at which it *changed* so the two are
distinguishable after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

FINGERPRINT_FIELDS: tuple[str, ...] = (
    "completion_phase",
    "body_len",
    "status",
    "streaming",
    "tool_pause",
    "liveness_observed_at",
)

# Bounded so an abort payload stays event-sized; the tail is what discriminates.
MAX_TRACE_ENTRIES = 12

# Fraction of ``no_progress_s`` below which a fingerprint counts as advancing.
_ADVANCING_RATIO = 0.25

# A fingerprint returning to an already-seen value twice is oscillation, not
# progress — a page that flips between two states forever looks "advancing" to
# a bare ``fp != last_fp`` comparison.
_OSCILLATION_REVISITS = 2


def fingerprint(snapshot: dict[str, Any]) -> tuple[Any, ...]:
    """Progress fingerprint of one poll snapshot, in ``FINGERPRINT_FIELDS`` order."""
    return tuple(snapshot.get(key) for key in FINGERPRINT_FIELDS)


@dataclass
class ProgressTrace:
    """Rolling record of progress-fingerprint changes across one poll loop."""

    max_entries: int = MAX_TRACE_ENTRIES
    changes: int = 0
    revisits: int = 0
    last_change_at_s: float = 0.0
    entries: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0
    phase: str | None = None
    _seen: set[tuple[Any, ...]] = field(default_factory=set)
    _current: tuple[Any, ...] | None = None

    def record(self, value: tuple[Any, ...], *, at_s: float) -> bool:
        """Absorb one polled fingerprint; True when it differs from the previous.

        The first call seeds the baseline and reports no change, matching the
        loop's own ``fp != last_fp`` semantics.
        """
        self.phase = _phase_of(value)
        if self._current is None:
            self._current = value
            self._seen.add(value)
            self._append(value, at_s=at_s)
            return False
        if value == self._current:
            return False
        self._current = value
        self.changes += 1
        self.last_change_at_s = at_s
        if value in self._seen:
            self.revisits += 1
        self._seen.add(value)
        self._append(value, at_s=at_s)
        return True

    def _append(self, value: tuple[Any, ...], *, at_s: float) -> None:
        entry: dict[str, Any] = {"at_s": round(at_s, 1)}
        entry.update(dict(zip(FINGERPRINT_FIELDS, value, strict=True)))
        self.entries.append(entry)
        while len(self.entries) > self.max_entries:
            self.entries.pop(0)
            self.dropped += 1

    def verdict(self, *, now_s: float, no_progress_s: float) -> str:
        """Name the shape of the trace: what the abort could not otherwise say."""
        if self.changes == 0:
            return "never_advanced"
        if self.revisits >= _OSCILLATION_REVISITS:
            return "oscillating"
        frozen_for = now_s - self.last_change_at_s
        if no_progress_s > 0 and frozen_for >= no_progress_s:
            return "frozen"
        if no_progress_s > 0 and frozen_for >= no_progress_s * _ADVANCING_RATIO:
            return "slowing"
        return "advancing"

    def as_dict(self, *, now_s: float, no_progress_s: float) -> dict[str, Any]:
        """Abort-payload form: verdict plus the evidence it was derived from."""
        return {
            "verdict": self.verdict(now_s=now_s, no_progress_s=no_progress_s),
            "changes": self.changes,
            "revisits": self.revisits,
            "frozen_for_s": round(max(now_s - self.last_change_at_s, 0.0), 1),
            "last_change_at_s": round(self.last_change_at_s, 1),
            "elapsed_s": round(now_s, 1),
            "no_progress_s": no_progress_s,
            "phase_at_abort": self.phase,
            "history": list(self.entries),
            "history_dropped": self.dropped,
        }


def _phase_of(value: tuple[Any, ...]) -> str | None:
    index = FINGERPRINT_FIELDS.index("completion_phase")
    phase = value[index] if index < len(value) else None
    return str(phase) if phase else None
