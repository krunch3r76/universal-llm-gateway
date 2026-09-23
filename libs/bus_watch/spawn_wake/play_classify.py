"""Leftover play classifier — hold | play | sit after go-under.

Overnight leftover work is one class, decided at spawn time (and planted as a
mode on ``--go-under``):

- **hold** — a live ``contract=conductor`` child (or a GIW row still hopping)
  already owns the addressed ``todo:{slug}``. Ticker doorbells. ``fire_spawn``
  refuses ``play_hold``. A consult reply on a terminal conductor is not that
  child: the bus thread stays open so the reply has a home, and ``seat_empty``
  clears the false owner so play can admit.
- **play** — ``now_row`` / induction / tip NEXT names ``todo:{slug}`` and no
  live conductor owns it. Admit that conductor via ``source_ref`` rematerialize.
  Not ``successor_contract=conductor`` on a house generate.
- **sit** — house attention remains and there is no addressed todo. Today's
  headless liaison successor (10534). ``--go-under --sit`` forces this class.

Bind (operator 2026-09-20): prefer hold over sit when unsure a conductor is
live (wrong-direction: a second liaison). Prefer play over sit when ``now_row``
names ``todo:`` even if the scoreboard is thin (wrong-direction: another
maestro). Play still requires positive evidence of *no* live owner — unsure
live + named todo ⇒ hold, not play.
"""

from __future__ import annotations

import re
from typing import Any

from bus_watch.now_row import resolve_now_row
from bus_watch.spawn_wake.packet import successor_model_fields

LEFTOVER_HOLD = "hold"
LEFTOVER_PLAY = "play"
LEFTOVER_SIT = "sit"
PLAY_HOLD = "play_hold"
MODE_AWARE = "aware"
MODE_SIT = "sit"

_TODO_RE = re.compile(r"\btodo:([a-z0-9][a-z0-9_-]*)\b", re.I)
_TERMINAL_LIFECYCLES = frozenset({"completed", "abandoned", "failed"})
_TERMINAL_STATUSES = frozenset({"closed", "failed"})
_LIVE_STATUSES = frozenset({"active", "waiting", "blocked"})
_LIVE_LIFECYCLES = frozenset({"admitted", "running", "in_flight", "hopping"})


def extract_todo_slug(text: object) -> str | None:
    """First ``todo:{slug}`` token in ``text``, or None."""
    match = _TODO_RE.search(str(text or ""))
    return match.group(1).lower() if match else None


def addressed_todo(digest: dict[str, Any]) -> str | None:
    """Todo slug for ticker play — roster rows first, then legacy NOW stack."""
    roster = digest.get("roster")
    if isinstance(roster, list) and roster:
        from bus_watch.roster import roster_play_todo

        slug, _verdict = roster_play_todo(digest)
        if slug:
            return slug
        return None
    raw, _source = resolve_now_row(digest)
    slug = extract_todo_slug(raw)
    if slug:
        return slug
    policy = digest.get("policy") or {}
    for blob in (
        policy.get("now_row"),
        digest.get("now_row"),
        (digest.get("induction") or {}).get("now")
        if isinstance(digest.get("induction"), dict)
        else None,
        digest.get("next"),
        (digest.get("root") or {}).get("last_subject"),
    ):
        slug = extract_todo_slug(blob)
        if slug:
            return slug
    return None


def leftover_mode(digest: dict[str, Any], state: dict[str, Any]) -> str:
    """``aware`` unless the operator forced ``--sit`` onto state/policy."""
    planted = (
        (state.get("play") or {}).get("mode")
        if isinstance(state.get("play"), dict)
        else None
    )
    policy = digest.get("policy") or state.get("policy") or {}
    raw = planted or policy.get("play") or MODE_AWARE
    return MODE_SIT if str(raw) == MODE_SIT else MODE_AWARE


def _lane_todos(lane: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for blob in (
        lane.get("work_key"),
        lane.get("source_ref"),
        lane.get("last_subject"),
        lane.get("slug"),
    ):
        slug = extract_todo_slug(blob)
        if slug:
            found.add(slug)
        elif blob and str(blob).lower().startswith("todo:"):
            found.add(str(blob).split(":", 1)[1].lower())
    return found


def _hopping(lane: dict[str, Any]) -> bool:
    tokens = lane.get("closeout_tokens") or []
    if isinstance(tokens, str):
        tokens = [tokens]
    joined = " ".join(str(t) for t in tokens)
    return bool(lane.get("hop_owed")) or "ROW_HOP" in joined


def _conductor_signal(lane: dict[str, Any]) -> bool | None:
    """True = conductor, False = known other, None = unsure."""
    contract = (
        str(lane.get("contract") or lane.get("packet_kind") or "").strip().lower()
    )
    if contract == "conductor":
        return True
    if contract:
        return False
    subject = str(lane.get("last_subject") or "")
    if (
        _hopping(lane)
        or "ROW_HOP" in subject
        or "contract=conductor" in subject.lower()
    ):
        return True
    if _lane_todos(lane):
        return None
    return False


def _consult_reply_lane(lane: dict[str, Any]) -> bool:
    """Latest turn is the consult answer, not a conductor still speaking."""
    if str(lane.get("last_from") or "") != "web-anthropic":
        return False
    subject = str(lane.get("last_subject") or "").strip().lower()
    return subject.startswith("cdp reply")


def _quiet_work_lane(lane: dict[str, Any]) -> bool:
    """Dispatch quiet-alarm after a conductor closeout, not the seat speaking."""
    if str(lane.get("last_from") or "") != "dispatch":
        return False
    subject = str(lane.get("last_subject") or "").strip().lower()
    return subject.startswith("quiet with work in flight")


def _continuation_surface(lane: dict[str, Any]) -> bool:
    return _consult_reply_lane(lane) or _quiet_work_lane(lane)


def consult_reply_seat_empty(thread_id: str) -> bool:
    """True when the terminal conductor still owes a consult continuation.

    Fail closed. An unreachable ledger leaves the lane looking live so the
    ticker holds instead of minting a second conductor.
    """
    from operator_hop_harvest.ledger import fetch_latest_terminal_conductor

    fetched = fetch_latest_terminal_conductor(thread_id)
    if not isinstance(fetched, dict) or fetched.get("ledger_unreachable"):
        return False
    row = fetched.get("row")
    if not isinstance(row, dict) or not row.get("consult_pending_continue_owed"):
        return False
    record = row.get("record_json")
    if isinstance(record, dict) and record.get("hop_successor"):
        return False
    return True


def hire_latch_released(digest: dict[str, Any], dispatch_id: str) -> bool:
    """True when *dispatch_id* is a terminal conductor that still owes a continuation.

    A quiet-alarm or consult-reply on the open bus thread is that surface.
    The hire latch must not block the re-admit.
    """
    target = str(dispatch_id or "").strip()
    if not target:
        return False
    from operator_hop_harvest.ledger import fetch_latest_terminal_conductor

    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return False
    for lane in lanes:
        if not isinstance(lane, dict) or not _continuation_surface(lane):
            continue
        thread_id = str(lane.get("id") or "")
        if not thread_id:
            continue
        fetched = fetch_latest_terminal_conductor(thread_id)
        if not isinstance(fetched, dict) or fetched.get("ledger_unreachable"):
            continue
        row = fetched.get("row")
        if not isinstance(row, dict) or not row.get("consult_pending_continue_owed"):
            continue
        record = row.get("record_json")
        if isinstance(record, dict) and record.get("hop_successor"):
            continue
        if str(row.get("dispatch_id") or "").strip() == target:
            return True
    return False


def mark_consult_reply_seats_empty(digest: dict[str, Any]) -> None:
    """Stamp ``seat_empty`` on conductor lanes whose consult reply is in.

    While the reply is still out, the lane is not a consult-reply lane, so
    the ticker keeps holding. Hopping stays live even if a stamp is present.
    """
    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return
    for lane in lanes:
        if not isinstance(lane, dict) or lane.get("seat_empty") is True:
            continue
        if _conductor_signal(lane) is not True or not _continuation_surface(lane):
            continue
        thread_id = str(lane.get("id") or "")
        if thread_id and consult_reply_seat_empty(thread_id):
            lane["seat_empty"] = True


def _lane_live(lane: dict[str, Any]) -> bool | None:
    """True = live, False = terminal, None = unsure."""
    if _hopping(lane):
        return True
    # Open bus thread after a consult reply: the seat is empty, the chat is not.
    if lane.get("seat_empty") is True:
        return False
    lifecycle = str(lane.get("lifecycle") or "").strip().lower()
    status = str(lane.get("status") or "").strip().lower()
    if (
        lifecycle in _TERMINAL_LIFECYCLES
        or status in _TERMINAL_STATUSES
        or lane.get("terminal")
    ):
        return False
    if status in _LIVE_STATUSES or lifecycle in _LIVE_LIFECYCLES:
        return True
    if not status and not lifecycle:
        return None
    return None


def live_conductor_owner(
    digest: dict[str, Any], todo_slug: str
) -> dict[str, Any] | None:
    """Return the owning lane, or a sentinel ``{"unsure": True}``.

    ``lanes is None`` means we did not observe — unsure. ``[]`` means we looked.
    """
    if "lanes" not in digest:
        return {"unsure": True, "reason": "lanes_unobserved"}
    lanes = digest.get("lanes")
    if lanes is None:
        return {"unsure": True, "reason": "lanes_unobserved"}
    mark_consult_reply_seats_empty(digest)
    slug = todo_slug.lower()
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        todos = _lane_todos(lane)
        live = _lane_live(lane)
        conductor = _conductor_signal(lane)
        if slug not in todos:
            continue
        if live is False:
            continue
        if live is True and conductor is True:
            return {"lane": lane, "unsure": False}
        if live is not False and conductor is not False:
            return {"lane": lane, "unsure": True, "reason": "conductor_or_live_unknown"}
    return None


def classify_leftover(
    digest: dict[str, Any],
    state: dict[str, Any] | None = None,
    *,
    lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return leftover class + reasons. ``lock`` reserved for callers; unused."""
    del lock
    st = state or {}
    mode = leftover_mode(digest, st)
    todo = addressed_todo(digest)
    result: dict[str, Any] = {
        "leftover": LEFTOVER_SIT,
        "reason": "sit_no_todo",
        "todo": todo,
        "mode": mode,
        "owner": None,
        "unsure_live": False,
    }
    if mode == MODE_SIT:
        result["reason"] = "sit_forced"
        return result
    if not todo:
        roster = digest.get("roster") or []
        if roster:
            from bus_watch.roster import (
                DECISION_HOLD,
                classify_roster_rows,
                roster_play_rows,
            )

            play_rows = roster_play_rows(digest)
            if play_rows:
                first_row, _verdict = play_rows[0]
                result["leftover"] = LEFTOVER_PLAY
                result["reason"] = "play_roster_row"
                result["todo"] = extract_todo_slug(first_row.get("work_key"))
                return result
            if any(
                v.get("decision") == DECISION_HOLD for v in classify_roster_rows(digest)
            ):
                result["leftover"] = LEFTOVER_HOLD
                result["reason"] = PLAY_HOLD
            return result
        return result
    owner = live_conductor_owner(digest, todo)
    result["owner"] = owner
    if owner is None:
        result["leftover"] = LEFTOVER_PLAY
        result["reason"] = "play_addressed_todo"
        return result
    result["leftover"] = LEFTOVER_HOLD
    result["reason"] = PLAY_HOLD
    result["unsure_live"] = bool(owner.get("unsure"))
    return result


def plant_play_state(
    state: dict[str, Any],
    *,
    mode: str = MODE_AWARE,
    as_of: str,
) -> dict[str, Any]:
    """Write classifier default onto tick state. Never sets successor_contract."""
    chosen = MODE_SIT if mode == MODE_SIT else MODE_AWARE
    policy = dict(state.get("policy") or {})
    policy["play"] = chosen
    state["policy"] = policy
    planted = {"mode": chosen, "as_of": as_of}
    state["play"] = planted
    return planted


def build_play_dispatch_body(
    root_id: str,
    policy: dict[str, Any],
    *,
    todo_slug: str,
    roster_row_id: str | None = None,
) -> dict[str, Any]:
    """Admit one conductor on the addressed todo. Lane B; no house-generate paste.

    The conductor model is ``policy.successor_model``, the same slug later
    wakes use. Omitting ``model=`` here used to resolve Composer Fast and
    split the house driver from the ticker successor.

    Coord parent is the resume root. ``loop_thread`` is occupancy (DIGEST),
    not conductor mailbox (a:36103 — 12029 play 422'd on tape 12030).
    """
    max_hop = int(policy.get("max_hop_minutes") or 60)
    work_key = f"todo:{todo_slug}"
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "conductor",
        "lane": "B",
        "source_ref": work_key,
        "work_key": work_key,
        "dispatch_thread_id": str(root_id),
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        **successor_model_fields(policy),
    }
    if roster_row_id:
        body["_roster_row_id"] = str(roster_row_id)
    return body


__all__ = [
    "LEFTOVER_HOLD",
    "LEFTOVER_PLAY",
    "LEFTOVER_SIT",
    "MODE_AWARE",
    "MODE_SIT",
    "PLAY_HOLD",
    "addressed_todo",
    "build_play_dispatch_body",
    "classify_leftover",
    "consult_reply_seat_empty",
    "hire_latch_released",
    "mark_consult_reply_seats_empty",
    "extract_todo_slug",
    "leftover_mode",
    "live_conductor_owner",
    "plant_play_state",
]
