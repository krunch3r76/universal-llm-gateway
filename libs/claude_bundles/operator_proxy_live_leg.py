"""Project the operator-proxy hop live leg from house-lane bus state."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

_ROSTER_REL = Path("notes/system/operational/maestro-sidekick-roster.md")
_EXTRA_HOUSE_LANES: tuple[str, ...] = ("15851", "12286")
_FETCH_LAST = 200

_SUBJECT_RE = re.compile(
    r"^(?P<verb>[A-Z_]+)"
    r"(?:\|(?:(?P<kind>implement|investigate|freeform|verify)\|)?(?P<key>[a-z_]+:[^\s|]+))?"
)
_TURN_REF_RE = re.compile(r"#(\d+)")
_LANE_ID_RE = re.compile(r"(\d{5})")

GOVERNING_RULE: dict[str, str] = {
    "REQUEST": (
        "R5: no PICKUP|<work_key> within 10 min ⇒ ask a live sidekick to recover "
        "the peer"
    ),
    "PICKUP": (
        "R3: read DONE / FAILED / QUESTION and nothing in between; answer QUESTION "
        "with RULING|<work_key>"
    ),
    "PLAN": (
        "R4: review the plan and post ACK or PARK before the implement leg"
    ),
    "LANDED": (
        "LANDED closes the REQUEST like DONE and leaves a restart-owed item until "
        "DONE or FAILED on the same work_key"
    ),
}

_REQUEST_CLOSE = frozenset({"DONE", "FAILED", "CANCEL", "WITHDRAW", "LANDED"})
_PICKUP_CLOSE = frozenset({"DONE", "FAILED"})
_PLAN_CLOSE = frozenset({"ACK", "PARK", "CANCEL"})

POINTERS_BLOCK = """\
- Prior LEG blocks (LEG 119…183): `cortex://notes/system/threads/<lane>-standing-handoff.md` and the house lanes listed above — still-open work is re-derived into the live leg; closed legs have no pending act.
- K-row rulings (K1–K22): runbook Appendix § Rulings (K-rows) — current binds are restated in step 5 R lines and in the hard refusals (K22 opus-5.5-extra, no fable).
- Conductor trajectories (2026-09-30): runbook Appendix § Conductor trajectories — C1 routes conductor lanes to the sidekick; `contract=conductor` is inline in hard refusals.
- Journal entries: `cortex://notes/system/maestro/journal.md` — narrative; first act 4 reads its head.
- Roster detail (bots, hop history): `cortex://notes/system/operational/maestro-sidekick-roster.md` — lane ids used at render are named in the running-now line.
- Wake/send_later text: runbook step 8 — one-shot wakes only; the refusal against re-arming send_later stays inline."""


@dataclass(frozen=True, slots=True)
class OpenItem:
    lane: str
    turn: int
    verb: str
    kind: str
    key: str
    subject: str


@dataclass
class LiveLeg:
    items: list[OpenItem] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    truncated: bool = False
    lanes_queried: list[str] = field(default_factory=list)


def load_maestro_roster_text() -> str:
    """Load the maestro sidekick roster from the cortex files root."""
    root = cortex_files_root()
    path = root / _ROSTER_REL
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def house_lanes(roster_text: str) -> list[str]:
    """Parse roster tables for lane ids, then append gc1/operator lanes."""
    seen: set[str] = set()
    ordered: list[str] = []
    for line in (roster_text or "").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) < 5 or cells[1] in {"N", "Display name", "---"}:
            continue
        if not cells[1].isdigit():
            continue
        match = _LANE_ID_RE.search(cells[3])
        if match is None:
            continue
        lane_id = match.group(1)
        if lane_id not in seen:
            seen.add(lane_id)
            ordered.append(lane_id)
    for extra in _EXTRA_HOUSE_LANES:
        if extra not in seen:
            seen.add(extra)
            ordered.append(extra)
    return ordered


def _parse_subject(subject: str) -> tuple[str, str, str]:
    text = (subject or "").strip()
    match = _SUBJECT_RE.match(text)
    if match is None:
        return "", "", ""
    verb = match.group("verb") or ""
    kind = match.group("kind") or ""
    key = match.group("key") or ""
    return verb, kind, key


def _cancel_targets_key(subject: str, turn_number: int, open_requests: dict[str, int]) -> str | None:
    _, _, key = _parse_subject(subject)
    if key:
        return key
    ref = _TURN_REF_RE.search(subject or "")
    if ref is None:
        return None
    req_turn = int(ref.group(1))
    for work_key, start_turn in open_requests.items():
        if start_turn == req_turn:
            return work_key
    return None


def fold_lane(lane: str, turns: list[dict]) -> tuple[list[OpenItem], bool]:
    """Fold *turns* into open items for one house lane."""
    open_requests: dict[str, tuple[int, str, str, str]] = {}
    open_pickups: dict[str, tuple[int, str, str, str]] = {}
    open_plans: dict[str, tuple[int, str, str, str]] = {}
    open_landeds: dict[str, tuple[int, str, str, str]] = {}
    request_start_turn: dict[str, int] = {}
    truncated = len(turns) >= _FETCH_LAST

    for turn in turns:
        turn_no = int(turn.get("turn_number") or turn.get("id") or 0)
        subject = str(turn.get("subject") or "")
        verb, kind, key = _parse_subject(subject)
        if not verb:
            continue

        if verb == "REQUEST" and key:
            open_requests[key] = (turn_no, verb, kind, subject)
            request_start_turn[key] = turn_no
        elif verb in _REQUEST_CLOSE:
            close_key = key
            if verb in {"CANCEL", "WITHDRAW"} and not close_key:
                close_key = _cancel_targets_key(subject, turn_no, request_start_turn)
            if close_key:
                open_requests.pop(close_key, None)
                if verb == "LANDED":
                    open_landeds[close_key] = (turn_no, verb, kind, subject)
                if verb in _PICKUP_CLOSE:
                    open_pickups.pop(close_key, None)
                    open_landeds.pop(close_key, None)
        elif verb == "PICKUP" and key:
            open_pickups[key] = (turn_no, verb, kind, subject)
        elif verb == "PLAN" and key:
            open_plans[key] = (turn_no, verb, kind, subject)
        elif verb == "IMPLEMENT" and key:
            open_plans.pop(key, None)
        elif verb in _PLAN_CLOSE and key:
            open_plans.pop(key, None)

    items: list[OpenItem] = []
    for store in (open_requests, open_pickups, open_plans, open_landeds):
        for work_key, (turn_no, verb, kind, subject) in store.items():
            items.append(
                OpenItem(
                    lane=lane,
                    turn=turn_no,
                    verb=verb,
                    kind=kind,
                    key=work_key,
                    subject=subject,
                )
            )
    items.sort(key=lambda item: (item.lane, item.turn))
    return items, truncated


def project_live_leg(
    lanes: list[str],
    fetch_turns: Callable[[str], list[dict]],
) -> LiveLeg:
    """Query each lane and merge open items."""
    leg = LiveLeg(lanes_queried=list(lanes))
    any_truncated = False
    for lane in lanes:
        try:
            turns = fetch_turns(lane)
        except Exception as exc:  # noqa: BLE001 — render degraded line per lane
            leg.errors[lane] = str(exc)
            continue
        items, truncated = fold_lane(lane, turns)
        leg.items.extend(items)
        any_truncated = any_truncated or truncated
    leg.items.sort(key=lambda item: (item.lane, item.turn))
    leg.truncated = any_truncated
    return leg


def render_live_leg(leg: LiveLeg) -> str:
    """Render the live-leg block for the hop template."""
    lines: list[str] = []
    if leg.truncated:
        lines.append(
            "(truncated: last=200 per lane; an open item may lie beyond this window.)"
        )
    for lane in leg.lanes_queried:
        if lane in leg.errors:
            lines.append(
                f"live leg unavailable for {lane}: {leg.errors[lane]}; "
                "read it with agent_bus_read fetch compact before any negative claim"
            )
    for item in leg.items:
        rule = GOVERNING_RULE.get(item.verb, "")
        kind_part = f"{item.kind}|" if item.kind else ""
        lines.append(
            f"- lane {item.lane} #{item.turn} {item.verb}|{kind_part}{item.key} — "
            f"{rule}"
        )
    if not lines:
        lines.append("(no open REQUEST, PICKUP, PLAN, or restart-owed LANDED items)")
    return "\n".join(lines)


__all__ = [
    "GOVERNING_RULE",
    "LiveLeg",
    "OpenItem",
    "POINTERS_BLOCK",
    "fold_lane",
    "house_lanes",
    "load_maestro_roster_text",
    "project_live_leg",
    "render_live_leg",
]
