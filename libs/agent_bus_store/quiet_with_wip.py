"""Quiet-with-WIP gate predicate (A′ — arc 6885).

Pure evaluation only: snapshot dataclass in, fire/skip verdict out — no I/O.
The bus-store watchdog gathers rows and calls ``evaluate_quiet_with_wip`` on a
timer; ``pickup_awaits`` write-path refuse remains the complementary cease gate
(its "unreachable from send/reply" scopes the call site, not this predicate).

Silence is **seat-scoped** (``last_turn_from(T, S)``), never ``threads.updated_at``
— worker closeouts that create debt must not reset the quiet clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Sequence

from claude_bundles.pickup_awaits import PriorTurn, find_unbound_pickup_turns

QuietReason = Literal["wip_in_flight", "closeout_unharvested", "pickup_unbound"]
SkipReason = Literal[
    "lifecycle_out",
    "alarm_open",
    "licensed_park",
    "not_silent",
    "no_wip",
]

_WORKER_AGENTS = frozenset({"dispatch", "cursor-sdk"})


@dataclass(frozen=True, slots=True)
class DispatchLinkView:
    """One ``thread_dispatch_links`` row as seen by the predicate."""

    execution_id: str
    terminal_status: str | None
    terminal_at: str | None = None


@dataclass(frozen=True, slots=True)
class LaneTurnView:
    """Minimal turn row for silence + unharvested + pickup scans."""

    turn_number: int
    from_agent: str
    created_at: str
    subject: str = ""
    body: str = ""


@dataclass(frozen=True, slots=True)
class QuietWithWipSnapshot:
    """All inputs the pure gate needs — gathered by the watchdog sweep."""

    thread_id: str
    seat: str
    now: datetime
    threshold_s: float
    lifecycle: str
    links: tuple[DispatchLinkView, ...]
    turns: tuple[LaneTurnView, ...]
    licensed_park: bool = False
    alarm_open: bool = False


@dataclass(frozen=True, slots=True)
class QuietWithWipVerdict:
    """Fire/skip decision with distinguishable ``reason`` for the event stream."""

    fire: bool
    reason: QuietReason | None = None
    skip_reason: SkipReason | None = None
    wip_execution_ids: tuple[str, ...] = ()


def parse_iso_ts(ts: str) -> datetime:
    """Parse bus ISO timestamps (``Z`` or offset) into aware UTC datetimes."""
    normalized = (ts or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def infer_seat(turns: Sequence[LaneTurnView]) -> str | None:
    """Pick the quiet-candidate seat: latest non-worker ``from_agent`` on the lane."""
    ordered = sorted(turns, key=lambda t: t.turn_number, reverse=True)
    for turn in ordered:
        if turn.from_agent not in _WORKER_AGENTS:
            return turn.from_agent
    return None


def _classify_wip(
    *,
    seat: str,
    links: Sequence[DispatchLinkView],
    turns: Sequence[LaneTurnView],
) -> tuple[QuietReason | None, tuple[str, ...]]:
    """Return ``(reason, execution_ids)`` for the first matching WIP class."""
    in_flight = [lnk for lnk in links if lnk.terminal_status is None]
    if in_flight:
        return "wip_in_flight", tuple(lnk.execution_id for lnk in in_flight)

    unharvested: list[DispatchLinkView] = []
    for lnk in links:
        if lnk.terminal_status is None or not lnk.terminal_at:
            continue
        term_at = parse_iso_ts(lnk.terminal_at)
        answered = any(
            t.from_agent == seat and parse_iso_ts(t.created_at) > term_at for t in turns
        )
        if not answered:
            unharvested.append(lnk)
    if unharvested:
        return (
            "closeout_unharvested",
            tuple(lnk.execution_id for lnk in unharvested),
        )

    prior = [
        PriorTurn(turn_number=t.turn_number, subject=t.subject, body=t.body)
        for t in turns
    ]
    unbound = find_unbound_pickup_turns(prior, closing_text="")
    if unbound:
        return "pickup_unbound", ()
    return None, ()


def evaluate_quiet_with_wip(snap: QuietWithWipSnapshot) -> QuietWithWipVerdict:
    """Evaluate quiet-with-WIP for one ``(lane, seat)`` snapshot.

    Returns ``fire=True`` only when WIP is open, the seat has been silent longer
    than ``threshold_s``, the lane is admitted/active, park is not licensed, and
    no alarm is already open (idempotence).
    """
    if snap.lifecycle not in ("admitted", "active"):
        return QuietWithWipVerdict(fire=False, skip_reason="lifecycle_out")
    if snap.alarm_open:
        return QuietWithWipVerdict(fire=False, skip_reason="alarm_open")
    if snap.licensed_park:
        return QuietWithWipVerdict(fire=False, skip_reason="licensed_park")

    reason, exec_ids = _classify_wip(
        seat=snap.seat, links=snap.links, turns=snap.turns
    )
    if reason is None:
        return QuietWithWipVerdict(fire=False, skip_reason="no_wip")

    seat_turns = [t for t in snap.turns if t.from_agent == snap.seat]
    if seat_turns:
        last_seat_at = max(parse_iso_ts(t.created_at) for t in seat_turns)
        silence_s = (snap.now - last_seat_at).total_seconds()
        if silence_s <= snap.threshold_s:
            return QuietWithWipVerdict(
                fire=False,
                skip_reason="not_silent",
                wip_execution_ids=exec_ids,
            )

    return QuietWithWipVerdict(
        fire=True,
        reason=reason,
        wip_execution_ids=exec_ids,
    )
