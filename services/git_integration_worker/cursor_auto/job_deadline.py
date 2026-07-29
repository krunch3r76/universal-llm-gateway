"""DIRECTIVE ``deadline:`` parsing — job TTL against a moved world.

Fable §5 reliability primitive: an enqueued job that sat past its deadline must
terminate ``status:failed reason=expired`` rather than execute stale intent
against a world that moved. Relative windows are measured from enqueue (the
job's own monotonic clock); absolute stamps are compared to wall clock.

An unparseable ``deadline:`` is a blocking authoring defect, never a silently
ignored field — a TTL that quietly does nothing is worse than no TTL.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

_DEADLINE_RE = re.compile(r"(?im)^[ \t]*deadline:[ \t]*(\S+)")
_RELATIVE_RE = re.compile(r"^\+?(\d+(?:\.\d+)?)([smh])$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1.0, "m": 60.0, "h": 3600.0}


@dataclass(frozen=True, slots=True)
class JobDeadline:
    """A parsed ``deadline:`` value — relative window or absolute instant."""

    raw: str
    relative_s: float | None = None
    absolute: datetime | None = None

    def expired(self, *, elapsed_s: float, now: datetime | None = None) -> bool:
        """True when this deadline has already passed."""
        if self.relative_s is not None:
            return elapsed_s > self.relative_s
        if self.absolute is not None:
            return (now or datetime.now(UTC)) > self.absolute
        return False


def parse_deadline(body: str) -> tuple[JobDeadline | None, str | None]:
    """Parse the DIRECTIVE ``deadline:`` line.

    Returns ``(deadline, bad_raw)``: an absent line yields ``(None, None)``; an
    unparseable value yields ``(None, <raw>)`` so the caller can block on it.
    """
    match = _DEADLINE_RE.search(body or "")
    if match is None:
        return None, None
    raw = match.group(1).strip().strip("`")
    relative = _RELATIVE_RE.match(raw)
    if relative is not None:
        amount = float(relative.group(1))
        unit = relative.group(2).lower()
        return JobDeadline(raw=raw, relative_s=amount * _UNIT_SECONDS[unit]), None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return JobDeadline(raw=raw, absolute=parsed), None


def job_elapsed_s(enqueued_at: float) -> float:
    """Seconds since the job was enqueued (monotonic, restart-safe within run)."""
    return max(0.0, time.monotonic() - enqueued_at)


@dataclass(frozen=True, slots=True)
class DeadlineVerdict:
    """What the handler should do about this job's ``deadline:`` line."""

    state: str  # absent | live | expired | unparseable
    raw: str | None = None
    elapsed_s: float = 0.0

    @property
    def blocking(self) -> bool:
        """True when the job must terminate instead of executing."""
        return self.state in {"expired", "unparseable"}


def deadline_verdict(body: str, *, enqueued_at: float) -> DeadlineVerdict:
    """Resolve a job's TTL state from its DIRECTIVE body and enqueue time.

    A job with no ``deadline:`` line has no TTL — Auto never invents one, so an
    absent field can never silently kill a long episode.
    """
    deadline, bad_raw = parse_deadline(body)
    if bad_raw is not None:
        return DeadlineVerdict(state="unparseable", raw=bad_raw)
    if deadline is None:
        return DeadlineVerdict(state="absent")
    elapsed = job_elapsed_s(enqueued_at)
    state = "expired" if deadline.expired(elapsed_s=elapsed) else "live"
    return DeadlineVerdict(state=state, raw=deadline.raw, elapsed_s=elapsed)


__all__ = [
    "DeadlineVerdict",
    "JobDeadline",
    "deadline_verdict",
    "job_elapsed_s",
    "parse_deadline",
]
