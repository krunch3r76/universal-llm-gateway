"""Leftover play classifier — hold | play | sit after go-under.

Overnight leftover work is one class, decided at spawn time (and planted as a
mode on ``--go-under``):

- **hold** — a live ``contract=conductor`` child (or a GIW row still hopping)
  already owns the addressed ``todo:{slug}``. Ticker doorbells. ``fire_spawn``
  refuses ``play_hold``. A consult reply on a terminal conductor is not that
  child: the bus thread stays open so the reply has a home, and ``seat_empty``
  clears the false owner so play can admit.
- **play** — ``now_row`` / induction / tip NEXT names ``todo:{slug}`` and no
  live conductor owns it. Admit the liaison once. The liaison admits the
  conductor. A live liaison dispatch is held. Not a house generate.
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
LIAISON_DRIVER_MARK = "liaison-sdk-driver"
LIAISON_TURN_SPEC = "cortex://notes/system/specs/liaison-sdk-driver-turn.md"
MODE_AWARE = "aware"
MODE_SIT = "sit"

_TODO_RE = re.compile(r"\btodo:([a-z0-9][a-z0-9_-]*)\b", re.I)
_TERMINAL_LIFECYCLES = frozenset({"completed", "abandoned", "failed"})
_TERMINAL_STATUSES = frozenset({"closed", "failed"})
_LIVE_STATUSES = frozenset({"active", "waiting", "blocked"})
_LIVE_LIFECYCLES = frozenset({"admitted", "running", "in_flight", "hopping"})
_OPEN_CONDUCTOR_LIFECYCLES = _LIVE_LIFECYCLES


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
    for tag in lane.get("tags") or []:
        token = str(tag).strip().lower()
        if token.startswith("todo:") and len(token) > 5:
            found.add(token.split(":", 1)[1])
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


def holder_lost_finished_hire(digest: dict[str, Any], todo_slug: str) -> bool:
    """A ``holder_lost`` conductor finished that hire. The row may be admitted again.

    The latch stores an earlier dispatch id. The dead hop's execution id does
    not match it, so id-matching release never fires and the row sits forever.
    """
    slug = str(todo_slug or "").strip().lower()
    if not slug:
        return False
    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return False
    for lane in lanes:
        if not isinstance(lane, dict) or slug not in _lane_todos(lane):
            continue
        if "holder_lost" in str(lane.get("last_subject") or "").lower():
            return True
    return False


def hire_latch_released(digest: dict[str, Any], dispatch_id: str) -> bool:
    """True when *dispatch_id* is a terminal conductor that still owes a continuation.

    A consult reply on the open bus thread is that surface. A quiet-with-WIP
    alarm on a lane that is still active is not: that lane still owns the row.
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
        if _quiet_work_lane(lane) and _lane_live(lane) is not False:
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
    return _parked_transport_owes_resume(lanes, target)


def _parked_transport_owes_resume(lanes: list[Any], target: str) -> bool:
    """A seat-written PARKED_TRANSPORT is not a finished hire.

    The latch dispatch and the parked closeout differ: hops stay on the
    thread, and the roster still names the first hire. Matching them would
    never resume. A later closeout whose dispatch id *is* the latch was
    already the resume, and stays held.
    """
    from operator_hop_harvest.ledger import fetch_latest_terminal_conductor

    for lane in lanes:
        if not isinstance(lane, dict) or _lane_live(lane) is not False:
            continue
        if _conductor_signal(lane) is not True:
            continue
        thread_id = str(lane.get("id") or "")
        if not thread_id:
            continue
        fetched = fetch_latest_terminal_conductor(thread_id)
        if not isinstance(fetched, dict) or fetched.get("ledger_unreachable"):
            continue
        row = fetched.get("row")
        if not isinstance(row, dict):
            continue
        parked_id = str(row.get("dispatch_id") or "").strip()
        if not parked_id or parked_id == target:
            continue
        record = row.get("record_json")
        body = (
            str(record.get("closeout_body") or "") if isinstance(record, dict) else ""
        )
        if "stop: PARKED_TRANSPORT" not in body:
            continue
        if "HOLD_MERGE" in body or "OPERATOR_GATE" in body:
            continue
        return True
    return False


def parked_resume(digest: dict[str, Any]) -> tuple[str, str] | None:
    """Closed lane whose latest closeout is an unpaid PARKED_TRANSPORT.

    Returns ``(thread_id, dispatch_id)`` so the replay hops that lane.
    """
    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return None
    from operator_hop_harvest.ledger import fetch_latest_terminal_conductor

    for lane in lanes:
        if not isinstance(lane, dict) or _lane_live(lane) is not False:
            continue
        if _conductor_signal(lane) is not True:
            continue
        thread_id = str(lane.get("id") or "")
        if not thread_id:
            continue
        fetched = fetch_latest_terminal_conductor(thread_id)
        if not isinstance(fetched, dict) or fetched.get("ledger_unreachable"):
            continue
        row = fetched.get("row")
        if not isinstance(row, dict):
            continue
        record = row.get("record_json")
        body = (
            str(record.get("closeout_body") or "") if isinstance(record, dict) else ""
        )
        if "stop: PARKED_TRANSPORT" not in body:
            continue
        if "HOLD_MERGE" in body or "OPERATOR_GATE" in body:
            continue
        if "land_disposition: discard" in body:
            continue
        dispatch_id = str(row.get("dispatch_id") or "").strip()
        if not dispatch_id:
            continue
        return thread_id, dispatch_id
    return None


def parked_resume_thread(digest: dict[str, Any]) -> str | None:
    """Worker thread whose latest closeout is an unpaid PARKED_TRANSPORT."""
    found = parked_resume(digest)
    return found[0] if found else None


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


def open_conductor_lanes(digest: dict[str, Any]) -> list[dict[str, Any]]:
    """House lanes whose lifecycle is still an admit or a running conductor.

    ``lifecycle=active`` is not enough. Stale bus rows sit there with no worker.
    ``admitted`` is a generate the ledger has not taken.
    """
    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return []
    open_lanes: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lifecycle = str(lane.get("lifecycle") or "").strip().lower()
        if lifecycle in _OPEN_CONDUCTOR_LIFECYCLES:
            open_lanes.append(lane)
    return open_lanes


def conductor_cap(policy: dict[str, Any] | None) -> int:
    """``policy.max_conductors``, default 2. A bad value stays at 2."""
    try:
        return max(int((policy or {}).get("max_conductors") or 2), 1)
    except (TypeError, ValueError):
        return 2


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
        if str(lane.get("quiet_reason") or "") == "closeout_unharvested":
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
    if live_liaison_lane(digest) is not None:
        result["leftover"] = LEFTOVER_HOLD
        result["reason"] = "live_liaison"
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
        open_lanes = open_conductor_lanes(digest)
        cap = conductor_cap(digest.get("policy") if isinstance(digest.get("policy"), dict) else {})
        admitted = [
            lane
            for lane in open_lanes
            if str(lane.get("lifecycle") or "").lower() == "admitted"
        ]
        if admitted or len(open_lanes) >= cap:
            result["leftover"] = LEFTOVER_HOLD
            result["reason"] = "admit_not_worker" if admitted else "conductor_cap"
            result["open_conductors"] = len(open_lanes)
            return result
        result["leftover"] = LEFTOVER_PLAY
        result["reason"] = "play_addressed_todo"
        return result
    result["leftover"] = LEFTOVER_HOLD
    result["reason"] = PLAY_HOLD
    result["unsure_live"] = bool(owner.get("unsure"))
    return result


def live_liaison_lane(digest: dict[str, Any]) -> dict[str, Any] | None:
    """Lane whose subject still names the liaison driver, when it is not finished.

    The admit subject is the marker the next tick can see. A closeout replaces
    that subject, so a finished liaison does not keep the seat.
    """
    lanes = digest.get("lanes")
    if not isinstance(lanes, list):
        return None
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        blob = f"{lane.get('last_subject') or ''} {lane.get('slug') or ''}"
        if LIAISON_DRIVER_MARK not in blob:
            continue
        if _lane_live(lane) is not False:
            return lane
    return None


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


def _play_prompt(todo_slug: str) -> str:
    """Liaison admit text. Generate rejects ``source_ref`` combined with ``prompt``."""
    if todo_slug == "liaison-multi-conductor-p3-multi-hire":
        # G5 is on the lane only. This hire is the G6 review. It does not land.
        return (
            "G5 is implemented on cursor-sdk/lane-12786 commit "
            "7f18840b09f91ba7a78e14b30211cc3ef11aaa6c, parent "
            "fc7421c31e9ec3beec5276647ddfef16b3c660f8. That commit is not on "
            "master. Do not land it. Do not mark G5 DONE. Do not re-implement. "
            "The live scoreboard sha256 is "
            "9d74c8108634d4ebda2bd03120c769444420dcb297350de908ef7615eac6d0df "
            "and its entry gate is still G4. The closeout prose cited "
            "4ccd1907; that digest is not the live file. Set the entry gate "
            "to G5 and write the in-flight line to this lane commit. "
            "Then run the G6 pre-land review: cdp/opus-5 purpose=review on "
            "the diff fc7421c3..7f18840. Exclude implement dispatch "
            "5e732bae5ae8-f9c62e11. Harvest the review onto the G6 sidecar. "
            "Stop before any merge. "
            "Scoreboard: cortex://notes/system/scoreboards/"
            "liaison-multi-conductor-p3-multi-hire-scoreboard.md."
        )
    if todo_slug == "liaison-ticker-steer-live-dispatch":
        return (
            "G7 is DONE. L1 2a005f508 is on master and the entry gate is "
            "complete. Do not re-admit this row."
        )
    text = (
        f"Execute the liaison turn at {LIAISON_TURN_SPEC}. "
        "Do not implement the row. Admit one conductor. "
        "If this dispatch is already live, stop. "
        "A closeout_unharvested lane is not a live conductor. "
        "If a cdp reply is already on this house, the conductor folds that "
        "harvest and continues. Do not stop for a human unless the packet "
        "names see-score or OPERATOR_GATE."
    )
    if todo_slug == "cdp-review-contract-shape":
        text += (
            " The conductor must rag(op=search) with scope research "
            "before it binds the review contract."
        )
    return text


def build_play_dispatch_body(
    root_id: str,
    policy: dict[str, Any],
    *,
    todo_slug: str,
    roster_row_id: str | None = None,
    reuse_thread: str | None = None,
    hop_from: str | None = None,
    stop_id: str | None = None,
) -> dict[str, Any]:
    """Admit the liaison once on an empty seat. The liaison admits the conductor.

    ``contract`` stays ``none`` so this dispatch does not implement the row.
    The subject carries ``liaison-sdk-driver`` because the digest lane keeps
    ``last_subject``, and the next tick holds on that mark.

    Coord parent is the resume root. ``loop_thread`` is occupancy (DIGEST),
    not conductor mailbox (a:36103 — 12029 play 422'd on tape 12030).
    """
    max_hop = int(policy.get("max_hop_minutes") or 60)
    # G5 is on the lane. This key is the G6 pre-land review.
    if todo_slug == "liaison-multi-conductor-p3-multi-hire":
        work_key = f"todo:{todo_slug}:g6-preland-review"
    elif todo_slug == "liaison-ticker-steer-live-dispatch":
        work_key = f"todo:{todo_slug}:g7-land"
    else:
        work_key = f"todo:{todo_slug}"
    body: dict[str, Any] = {
        "op": "generate",
        "seat": "cursor-sdk",
        "contract": "none",
        "lane": "B",
        "work_key": work_key,
        "dispatch_thread_id": str(root_id),
        "subject": f"{LIAISON_DRIVER_MARK} todo:{todo_slug}",
        "prompt": _play_prompt(todo_slug),
        "timeout_seconds": max_hop * 60 + 1800,
        "caller_agent": "liaison-ticker",
        **successor_model_fields(policy),
    }
    if roster_row_id:
        body["_roster_row_id"] = str(roster_row_id)
    if reuse_thread:
        body["reuse_thread"] = str(reuse_thread)
    if hop_from:
        body["hop_from"] = str(hop_from)
        body["hop_seq"] = 2
        body["hop_reason"] = "park_harvest"
    elif stop_id:
        body["_stop_id"] = str(stop_id)
    return body


__all__ = [
    "LEFTOVER_HOLD",
    "LEFTOVER_PLAY",
    "LEFTOVER_SIT",
    "MODE_AWARE",
    "MODE_SIT",
    "PLAY_HOLD",
    "addressed_todo",
    "LIAISON_DRIVER_MARK",
    "build_play_dispatch_body",
    "classify_leftover",
    "live_liaison_lane",
    "consult_reply_seat_empty",
    "hire_latch_released",
    "mark_consult_reply_seats_empty",
    "extract_todo_slug",
    "leftover_mode",
    "live_conductor_owner",
    "plant_play_state",
]
