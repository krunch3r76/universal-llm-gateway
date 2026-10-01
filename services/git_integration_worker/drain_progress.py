"""Shared drain occupancy-progress / stall predicate (R1′ / R2′).

Evaluated GIW-side where Auto and SDK heartbeat ledgers live. Manage reads the
same verdict from ``drain_state()["stalled"]`` so the Auto belt and telemetry
cannot drift. Stall does not arm supervisor SIGTERM.

Progress (R1′)::

    count-decrease ∨ occupancy op-set turnover ∨ fresh class-heartbeat

Admission tickets have no heartbeat; turnover is their honest signal (no
schema change). Auto / SDK rows may carry ``heartbeat_age_s`` or
``last_heartbeat_at``.

Stall (R2′)::

    (count == 0 ∧ completed-unconsumed past grace)
    ∨ (count > 0 ∧ ¬progress across stall_window)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Auto heartbeat cadence is ≤2s; SDK poll / GIW health heartbeat_ttl_s is ~30s.
# k≈3 of that slower class so a live cursor-sdk occupant is not stalled.
# Tickets rely on turnover, not this TTL. These feed R1 progress / telemetry
# and the Auto belt — they must not arm a supervisor kill.
HEARTBEAT_TTL_S = 90.0
# A claim whose heartbeat moved, then stopped for two poll intervals, is not
# a live holder. Admission tickets (cursor-sdk closeout included) have no
# ``claimed_at`` and stay busy until ``close_ticket`` — copying
# ``last_heartbeat_at`` onto the ticket must not finish the drain mid-closeout.
HEARTBEAT_INTERVAL_S = 30.0
DEAD_HEARTBEAT_AFTER_S = 2.0 * HEARTBEAT_INTERVAL_S
STALL_WINDOW_S = 90.0
# Above the ~2 min completed→SIGTERM lookalike window named in the bind.
COMPLETED_UNCONSUMED_GRACE_S = 150.0


def claim_heartbeat_dead(
    op: dict[str, Any], *, now: datetime | None = None
) -> bool:
    """True only for a claimed row whose heartbeat writer stopped.

    No ``claimed_at`` (admission tickets, including a cursor-sdk closeout)
    stays occupancy. A stamp still equal to ``claimed_at`` was never bumped;
    that age is time since claim and stays occupancy.
    """
    claimed = op.get("claimed_at")
    if not isinstance(claimed, str) or not claimed:
        return False
    age = _heartbeat_age_s(op, now=now)
    if age is None or age <= DEAD_HEARTBEAT_AFTER_S:
        return False
    last = op.get("last_heartbeat_at")
    if isinstance(last, str) and last == claimed:
        return False
    return True


def drain_busy_from_ops(
    ops: list[dict[str, Any]], *, now: datetime | None = None
) -> dict[str, Any]:
    """Busy verdict. Dead claimed heartbeats are not busy; open tickets are."""
    live: list[dict[str, Any]] = []
    dead: list[dict[str, Any]] = []
    for op in ops:
        if claim_heartbeat_dead(op, now=now):
            dead.append(op)
        else:
            live.append(op)
    if live:
        return {
            "busy": True,
            "determination": "busy",
            "live_ops": live,
            "dead_heartbeat_ops": dead,
        }
    if dead:
        return {
            "busy": False,
            "determination": "dead_heartbeat",
            "live_ops": live,
            "dead_heartbeat_ops": dead,
        }
    return {
        "busy": False,
        "determination": "idle",
        "live_ops": live,
        "dead_heartbeat_ops": dead,
    }


def reclassify_dead_heartbeat_work(
    detail: dict[str, Any], *, busy: bool
) -> tuple[bool, dict[str, Any]]:
    """Drop a probe whose only holders are dead claimed heartbeats.

    An open cursor-sdk ticket has no ``claimed_at`` and stays busy through
    closeout. This does not write intent rows.
    """
    ops = detail.get("active_ops")
    if isinstance(ops, list) and ops:
        verdict = drain_busy_from_ops(ops)
        if verdict["determination"] == "dead_heartbeat":
            updated = dict(detail)
            updated["busy"] = False
            updated["active_count"] = 0
            updated["active_ops"] = []
            updated["determination"] = "dead_heartbeat"
            updated["dead_heartbeat_ops"] = verdict["dead_heartbeat_ops"]
            return False, updated
        if verdict["dead_heartbeat_ops"] and verdict["live_ops"]:
            updated = dict(detail)
            updated["active_ops"] = verdict["live_ops"]
            updated["active_count"] = len(verdict["live_ops"])
            updated["busy"] = True
            return True, updated
    return busy, detail


def occupancy_op_ids(ops: list[dict[str, Any]]) -> frozenset[str]:
    """Stable identity set of occupancy op_ids used for turnover detection."""
    return frozenset(str(op.get("op_id")) for op in ops if op.get("op_id") is not None)


def _heartbeat_age_s(
    op: dict[str, Any], *, now: datetime | None = None
) -> float | None:
    age = op.get("heartbeat_age_s")
    if isinstance(age, int | float):
        return float(age)
    last = op.get("last_heartbeat_at")
    if not isinstance(last, str) or not last:
        return None
    try:
        then = datetime.fromisoformat(last)
    except ValueError:
        return None
    clock = now or datetime.now(then.tzinfo or UTC)
    if then.tzinfo is None and clock.tzinfo is not None:
        then = then.replace(tzinfo=clock.tzinfo)
    return max(0.0, (clock - then).total_seconds())


def heartbeat_fresh(
    ops: list[dict[str, Any]],
    *,
    ttl_s: float = HEARTBEAT_TTL_S,
    now: datetime | None = None,
) -> bool:
    """True when any occupancy row with a class heartbeat is inside TTL."""
    for op in ops:
        age = _heartbeat_age_s(op, now=now)
        if age is not None and age <= ttl_s:
            return True
    return False


def is_progress(
    *,
    prev_count: int | None,
    prev_ids: frozenset[str] | None,
    count: int,
    ids: frozenset[str],
    ops: list[dict[str, Any]],
    ttl_s: float = HEARTBEAT_TTL_S,
    now: datetime | None = None,
) -> bool:
    """R1′ progress term. First sample (prev None) is not a decrease/turnover."""
    if prev_count is not None and count < prev_count:
        return True
    if prev_ids is not None and ids != prev_ids:
        return True
    return heartbeat_fresh(ops, ttl_s=ttl_s, now=now)


def stalled_from_terms(
    *,
    count: int,
    r1_stalled: bool,
    completed_unconsumed: bool,
) -> bool:
    """R2′ shared stalled predicate — one definition for belt and keep-await."""
    if count == 0 and completed_unconsumed:
        return True
    return count > 0 and r1_stalled


@dataclass(slots=True)
class OccupancyProgressTracker:
    """Fold occupancy samples into R1′ progress and R2′ stalled."""

    stall_window_s: float = STALL_WINDOW_S
    heartbeat_ttl_s: float = HEARTBEAT_TTL_S
    completed_grace_s: float = COMPLETED_UNCONSUMED_GRACE_S
    last_count: int | None = None
    last_ids: frozenset[str] | None = None
    last_progress_mono: float | None = None
    completed_mono: float | None = None
    _started_mono: float | None = field(default=None, repr=False)

    def reset(self, now_mono: float) -> None:
        """New drain epoch — grace window starts now."""
        self.last_count = None
        self.last_ids = None
        self.last_progress_mono = now_mono
        self.completed_mono = None
        self._started_mono = now_mono

    def note_completed(self, now_mono: float) -> None:
        """Record drain.completed emit so unconsumed-grace can elapse."""
        if self.completed_mono is None:
            self.completed_mono = now_mono

    def observe(self, ops: list[dict[str, Any]], *, now_mono: float) -> bool:
        """Update last sample. Returns whether this sample is R1′ progress."""
        count = len(ops)
        ids = occupancy_op_ids(ops)
        progressed = is_progress(
            prev_count=self.last_count,
            prev_ids=self.last_ids,
            count=count,
            ids=ids,
            ops=ops,
            ttl_s=self.heartbeat_ttl_s,
        )
        if self.last_progress_mono is None or progressed:
            self.last_progress_mono = now_mono
        self.last_count = count
        self.last_ids = ids
        return progressed

    def stalled(self, ops: list[dict[str, Any]], *, now_mono: float) -> bool:
        """R2′ stalled after folding this sample."""
        self.observe(ops, now_mono=now_mono)
        r1_stalled = (
            self.last_progress_mono is not None
            and (now_mono - self.last_progress_mono) >= self.stall_window_s
        )
        unconsumed = (
            self.completed_mono is not None
            and (now_mono - self.completed_mono) >= self.completed_grace_s
        )
        return stalled_from_terms(
            count=len(ops),
            r1_stalled=r1_stalled,
            completed_unconsumed=unconsumed,
        )
