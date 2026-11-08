"""Quiet-with-WIP gate predicate (A′ — arc 6885).

Pure evaluation only: snapshot dataclass in, fire/skip verdict out — no I/O.
The bus-store watchdog gathers rows and calls ``evaluate_quiet_with_wip`` on a
timer; ``pickup_awaits`` write-path refuse remains the complementary cease gate
(its "unreachable from send/reply" scopes the call site, not this predicate).

Silence is **seat-scoped** (``last_turn_from(T, S)``), never ``threads.updated_at``
— worker closeouts that create debt must not reset the quiet clock.

The execution named on the latest ``TYPE: SEAT_REGISTRATION`` stamp is the
generate that holds the operator seat. It is omitted from the WIP set.
A dispatch the seat commissioned stays in the set until harvested.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from claude_bundles.pickup_awaits import (
    PriorTurn,
    find_unbound_pickup_turns,
    format_unbound_pickup_labels,
)

from .producer_projection import nonterminal_link_state

QuietReason = Literal["wip_in_flight", "closeout_unharvested", "pickup_unbound"]
SkipReason = Literal[
    "lifecycle_out",
    "alarm_open",
    "licensed_park",
    "not_silent",
    "no_wip",
    "refused_admit",
]

_WORKER_AGENTS = frozenset({"dispatch", "cursor-sdk"})
_PRE_ADMISSION_REFUSED_CODES = frozenset(
    {
        "CURSOR_SOURCE_REF_IN_FLIGHT",
        "CURSOR_WORK_FINGERPRINT_IN_FLIGHT",
        "CURSOR_WRITE_LEASE_HELD",
        "CURSOR_RESUME_BEHIND_CALLER",
        "CURSOR_RESUME_ALREADY_ADMITTED",
    }
)
_FAILED_SUBJECT_CODE_RE = re.compile(r"\bcode=([^|]+)")


@dataclass(frozen=True, slots=True)
class DispatchLinkView:
    """One ``thread_dispatch_links`` row as seen by the predicate."""

    execution_id: str
    terminal_status: str | None
    terminal_at: str | None = None
    linked_at: str | None = None
    pipeline_id: str | None = None
    liveness_witness: Literal["live", "dead"] | None = None


@dataclass(frozen=True, slots=True)
class LaneTurnView:
    """Minimal turn row for silence + unharvested + pickup scans."""

    turn_number: int
    from_agent: str
    created_at: str
    subject: str = ""
    body: str = ""
    read_at: str | None = None


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
    unbound_turn_labels: tuple[str, ...] = ()


def parse_iso_ts(ts: str) -> datetime:
    """Parse bus ISO timestamps (``Z`` or offset) into aware UTC datetimes."""
    normalized = (ts or "").replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def seat_park_licence(turns: Sequence[LaneTurnView], seat: str) -> bool:
    """True when the seat's latest ``TYPE:`` line is ``TYPE: PARKED``.

    A later non-``TYPE:`` turn (a CDP reply pasted onto the lane) does not
    cancel the licence. The next ``TYPE:`` line does.
    """
    ordered = sorted(
        (t for t in turns if t.from_agent == seat),
        key=lambda t: t.turn_number,
        reverse=True,
    )
    for turn in ordered:
        first = (turn.body or "").strip().split("\n", 1)[0].strip()
        if not first.startswith("TYPE:"):
            continue
        return first == "TYPE: PARKED" or first.startswith("TYPE: PARKED ")
    return False


def infer_seat(turns: Sequence[LaneTurnView]) -> str | None:
    """Pick the quiet-candidate seat: latest non-worker ``from_agent`` on the lane."""
    ordered = sorted(turns, key=lambda t: t.turn_number, reverse=True)
    for turn in ordered:
        if turn.from_agent not in _WORKER_AGENTS:
            return turn.from_agent
    return None


_SEAT_REGISTRATION_MARK = "TYPE: SEAT_REGISTRATION"
# First token after execution_id, whether the separator is ":", "=", or space.
_EXECUTION_ID_RE = re.compile(r"execution_id(?:\s*[:=]\s*|\s+)(\S+)")


def seat_holding_execution_id(turns: Sequence[LaneTurnView]) -> str | None:
    """Return the execution that currently holds the operator seat.

    Takes the first ``execution_id`` token in the latest
    ``TYPE: SEAT_REGISTRATION`` body. The token may sit mid-line; the
    separator may be a colon, an equals sign, or whitespace. That id is
    the seat's own generate, not a job the seat commissioned. Returns None
    when no stamp exists or the latest stamp has no id. No I/O.
    """
    ordered = sorted(turns, key=lambda t: t.turn_number, reverse=True)
    for turn in ordered:
        body = turn.body or ""
        first = body.strip().split("\n", 1)[0].strip()
        if not first.startswith(_SEAT_REGISTRATION_MARK):
            continue
        match = _EXECUTION_ID_RE.search(body)
        if match is None:
            return None
        return match.group(1)
    return None


def _links_excluding_seat_holder(
    links: Sequence[DispatchLinkView],
    turns: Sequence[LaneTurnView],
) -> list[DispatchLinkView]:
    """Drop the seat-holding generate so it cannot count as unharvested WIP."""
    holder = seat_holding_execution_id(turns)
    if not holder:
        return list(links)
    return [lnk for lnk in links if lnk.execution_id != holder]


def _turn_binds_execution_id(turn: LaneTurnView, execution_id: str) -> bool:
    """True when the turn subject/body names this dispatch execution."""
    subj = turn.subject or ""
    if f"exec={execution_id}" in subj or f"execution_id={execution_id}" in subj:
        return True
    return execution_id in (turn.body or "")


def _parse_failed_subject_code(subject: str) -> str | None:
    if not (subject or "").startswith("FAILED|"):
        return None
    match = _FAILED_SUBJECT_CODE_RE.search(subject)
    return match.group(1).strip() if match else None


def _is_pre_admission_failed_code(code: str | None) -> bool:
    if not code:
        return False
    return code in _PRE_ADMISSION_REFUSED_CODES


def _failed_turn_for_link(
    lnk: DispatchLinkView, turns: Sequence[LaneTurnView]
) -> LaneTurnView | None:
    for turn in turns:
        subject = turn.subject or ""
        if not subject.startswith("FAILED|"):
            continue
        if not _turn_binds_execution_id(turn, lnk.execution_id):
            continue
        return turn
    return None


def _link_is_refused_admit(
    lnk: DispatchLinkView,
    turns: Sequence[LaneTurnView],
    seat: str,
) -> bool:
    """Refused pre-admission: FAILED| for this execution, pre-admit code, no seat harvest."""
    if lnk.pipeline_id != "cursor-sdk-generate" or lnk.terminal_status != "failed":
        return False
    if not lnk.terminal_at:
        return False
    term_at = parse_iso_ts(lnk.terminal_at)
    if any(
        t.from_agent == seat and parse_iso_ts(t.created_at) > term_at for t in turns
    ):
        return False
    failed_turn = _failed_turn_for_link(lnk, turns)
    if failed_turn is None:
        return False
    return _is_pre_admission_failed_code(_parse_failed_subject_code(failed_turn.subject))


def _link_harvested(
    lnk: DispatchLinkView,
    turns: Sequence[LaneTurnView],
    seat: str,
) -> bool:
    """True when the seat answered after terminal or this link's closeout was read."""
    if not lnk.terminal_at:
        return False
    term_at = parse_iso_ts(lnk.terminal_at)
    if any(
        t.from_agent == seat and parse_iso_ts(t.created_at) > term_at for t in turns
    ):
        return True
    window_start = term_at - timedelta(seconds=5)
    for turn in turns:
        if turn.from_agent not in _WORKER_AGENTS:
            continue
        if not _turn_binds_execution_id(turn, lnk.execution_id):
            continue
        created = parse_iso_ts(turn.created_at)
        if created < window_start:
            continue
        if turn.read_at:
            return True
    return False


def _link_counts_as_live_wip(lnk: DispatchLinkView, *, now: datetime) -> bool:
    """True only when the link carries a positive liveness signal.

    A null ``terminal_status`` whose ``linked_at`` is missing or past the
    admit grace is ``unknown``, not live WIP, unless ``liveness_witness`` is
    ``live`` (a:36832). This does not write ``terminal_status``.
    """
    if lnk.terminal_status is not None:
        return False
    state, _reason = nonterminal_link_state(
        linked_at=lnk.linked_at,
        now=now,
        liveness_witness=lnk.liveness_witness,
    )
    return state == "in_flight"


def _classify_wip(
    *,
    seat: str,
    links: Sequence[DispatchLinkView],
    turns: Sequence[LaneTurnView],
    now: datetime,
) -> tuple[QuietReason | None, tuple[str, ...], tuple[str, ...]]:
    """Return ``(reason, execution_ids, unbound_turn_labels)`` for first WIP class.

    The seat-holding generate (latest ``SEAT_REGISTRATION`` execution) is
    removed before either in-flight or unharvested classification.
    """
    counted = _links_excluding_seat_holder(links, turns)
    counted = [lnk for lnk in counted if not _link_is_refused_admit(lnk, turns, seat)]
    in_flight = [lnk for lnk in counted if _link_counts_as_live_wip(lnk, now=now)]
    if in_flight:
        return (
            "wip_in_flight",
            tuple(lnk.execution_id for lnk in in_flight),
            (),
        )

    unharvested: list[DispatchLinkView] = []
    for lnk in counted:
        if lnk.terminal_status is None or not lnk.terminal_at:
            continue
        if not _link_harvested(lnk, turns, seat):
            unharvested.append(lnk)
    if unharvested:
        return (
            "closeout_unharvested",
            tuple(lnk.execution_id for lnk in unharvested),
            (),
        )

    prior = [
        PriorTurn(turn_number=t.turn_number, subject=t.subject, body=t.body)
        for t in turns
    ]
    unbound = find_unbound_pickup_turns(prior, closing_text="")
    if unbound:
        return "pickup_unbound", (), format_unbound_pickup_labels(unbound)
    return None, (), ()


def evaluate_quiet_with_wip(snap: QuietWithWipSnapshot) -> QuietWithWipVerdict:
    """Evaluate quiet-with-WIP for one ``(lane, seat)`` snapshot.

    Returns ``fire=True`` only when WIP is open, the seat has been silent longer
    than ``threshold_s``, the lane is admitted/active, park is not licensed, and
    no alarm is already open (idempotence). The generate named on the latest
    seat-registration stamp does not count as WIP.
    """
    if snap.lifecycle not in ("admitted", "active"):
        return QuietWithWipVerdict(fire=False, skip_reason="lifecycle_out")
    if snap.alarm_open:
        return QuietWithWipVerdict(fire=False, skip_reason="alarm_open")
    if snap.licensed_park:
        return QuietWithWipVerdict(fire=False, skip_reason="licensed_park")

    reason, exec_ids, unbound_labels = _classify_wip(
        seat=snap.seat, links=snap.links, turns=snap.turns, now=snap.now
    )
    if reason is None:
        all_links = _links_excluding_seat_holder(snap.links, snap.turns)
        if all_links and all(
            _link_is_refused_admit(lnk, snap.turns, snap.seat) for lnk in all_links
        ):
            return QuietWithWipVerdict(fire=False, skip_reason="refused_admit")
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
                unbound_turn_labels=unbound_labels,
            )

    return QuietWithWipVerdict(
        fire=True,
        reason=reason,
        wip_execution_ids=exec_ids,
        unbound_turn_labels=unbound_labels,
    )
