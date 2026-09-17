"""Pending-spawn mutex and wake filters for gear-3 liaison.

``tick_spawn_on_wake`` and ``evaluate_spawn_predicate`` call this module so a
finished worker cannot hold the house forever and the same closeout cannot wake
successor after successor. The digest already lists those lanes; the predicate
must read lifecycle/status instead of treating any ``pending_spawn`` dict as live.

Wake sources the ticker honours: an unread live lane; a finished *work* lane's
closeout, once (``served_closeouts``); a ``go under`` handoff, once
(``handoff`` / ``handoff_spawned_seq``); ``checkpoint_due``, once per CP epoch;
an undispositioned friction on a charter-owned service, once per assertion id
(``friction_rows_seen``, see ``bus_watch.friction_rows``).
A successor's own closeout never wakes the next successor — that loop is the
mill that minted four unasked Opus liaisons on 10534 (2026-09-12).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bus_watch.friction_rows import latch_rows
from bus_watch.ide_budget import (
    AGENT_TRANSCRIPTS,
    ide_holder_idle_s,
    ide_transcript_probe_resolved,
)

# ``abandoned`` = GIW orphaned the worker before it ran ("Dispatch orphaned —
# worker terminated before completion"); such a lane is finished, and treating it
# as live unread fed the 10479 remint-cap mill (2026-09-13, 169 refused spawns).
_TERMINAL_LIFECYCLES = frozenset(
    {"completed", "failed", "cancelled", "closed", "abandoned"}
)
_REMINT_CAP_CODE = "CURSOR_WORK_KEY_REMINT_CAP"
_SUCCESSOR_SUBJECT_MARK = "caller=liaison-ticker"
_SERVED_KEEP = 200
_SUCCESSOR_THREADS_KEEP = 50
IDE_IDLE_FORFEIT_S = 1200.0
_RELAY_FROM = "cursor-auto"
_PROGRESS_SUBJECT_RE = re.compile(r"^progress\s+—\s+elapsed\b", re.I)
_PROGRESS_JSON_RE = re.compile(
    r'^\s*\{\s*"summary"\s*:\s*"Still running',
    re.M,
)
_ROOT_SUCCESSOR_TERMINAL_RE = re.compile(
    r"CHECKPOINT|CLOSEOUT|\bSTAY\b|TYPE:\s*(CHECKPOINT|CLOSEOUT|STAY)",
    re.I,
)


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def row_is_terminal(row: dict[str, Any]) -> bool:
    """True when a digest lane or attention item is a finished worker.

    Uses ``status`` / ``lifecycle`` only. Subject-regex ``terminal`` is a
    CLOSEOUT/CHECKPOINT hint and can fire on a still-running seat.
    """
    if str(row.get("status") or "") == "closed":
        return True
    return str(row.get("lifecycle") or "").lower() in _TERMINAL_LIFECYCLES


def _first_type_line(body: str) -> str | None:
    """First ``TYPE:`` line in a turn body (programmatic envelope marker)."""
    for line in str(body or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("TYPE:"):
            return stripped
    return None


def _is_progress_heartbeat(*, from_agent: str, subject: str, body: str) -> bool:
    """``cursor-auto`` dispatch progress turns (``dispatch_progress.py``)."""
    if from_agent != _RELAY_FROM:
        return False
    if _PROGRESS_SUBJECT_RE.match(str(subject or "").strip()):
        return True
    return bool(_PROGRESS_JSON_RE.match(str(body or "")))


def _relay_non_actionable_kind(*, from_agent: str, subject: str, body: str) -> bool:
    """Post-terminal relay turns emitted by ``cursor-auto`` from code, not model prose."""
    if from_agent != _RELAY_FROM:
        return False
    if _is_progress_heartbeat(from_agent=from_agent, subject=subject, body=body):
        return True
    type_line = _first_type_line(body)
    if type_line is None:
        return False
    upper = type_line.upper()
    return upper.startswith("TYPE: WAKE") or upper.startswith("TYPE: CLOSEOUT")


def actionable_kind(turn: dict[str, Any]) -> bool:
    """True when an unread turn after the served mark deserves a wake.

    Classifier keys only on ``from`` / ``from_agent``, ``subject``, and the
    programmatic ``TYPE:`` first line. Unrecognized kinds fail closed (wake).
    """
    from_agent = str(turn.get("from") or turn.get("from_agent") or "")
    if not from_agent:
        return True
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    if _relay_non_actionable_kind(from_agent=from_agent, subject=subject, body=body):
        return False
    return True


def _unread_turns_for_item(item: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = item.get("unread_turns")
    if not isinstance(raw, list):
        return None
    return [row for row in raw if isinstance(row, dict)]


def _served_mark(served: dict[str, Any], lane_id: str) -> int:
    raw = served.get(lane_id)
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _lane_turns(item: dict[str, Any]) -> int | None:
    raw = item.get("turns")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _live_lane_actionable(item: dict[str, Any], served_lanes: dict[str, Any]) -> bool:
    """Non-terminal lane: actionable when ``turns`` exceeds ``served_lanes`` mark.

    Absent or unparseable ``turns`` fails closed (actionable), matching the
    ``unread_turns`` posture for terminal lanes.
    """
    turns = _lane_turns(item)
    if turns is None:
        return True
    lane_id = str(item.get("id") or "")
    return turns > _served_mark(served_lanes, lane_id)


def _terminal_lane_has_actionable_unread(
    item: dict[str, Any], *, served_mark: int
) -> bool:
    """True when some unread turn after ``served_mark`` is an actionable kind."""
    unread_turns = _unread_turns_for_item(item)
    if unread_turns is None:
        return True
    pending = [
        row for row in unread_turns if int(row.get("turn_number") or 0) > served_mark
    ]
    if not pending:
        return False
    return any(actionable_kind(row) for row in pending)


def _closeout_high_water(item: dict[str, Any]) -> int:
    unread_turns = _unread_turns_for_item(item)
    if unread_turns:
        return max(int(row.get("turn_number") or 0) for row in unread_turns)
    return int(item.get("turns") or 0)


def compact_unread_turn_summaries(turns: Any) -> list[dict[str, Any]]:
    """Shrink bus turn rows to the fields ``actionable_kind`` reads."""
    if not isinstance(turns, list):
        return []
    out: list[dict[str, Any]] = []
    for row in turns:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "turn_number": row.get("turn_number"),
                "from": row.get("from") or row.get("from_agent"),
                "subject": row.get("subject"),
                "body": row.get("body") or "",
            }
        )
    return out


def build_attention_lanes(
    lanes: list[dict[str, Any]],
    *,
    fetch_unread_turns: Callable[[str], Any],
) -> list[dict[str, Any]]:
    """Unread, non-nag lanes with gate-B ``unread_turns`` on terminal rows."""
    attention = [
        lane for lane in lanes if (lane.get("unread") or 0) > 0 and not lane.get("nag")
    ]
    enrich_terminal_attention_turns(attention, fetch_unread_turns=fetch_unread_turns)
    return attention


def enrich_terminal_attention_turns(
    attention: list[dict[str, Any]],
    *,
    fetch_unread_turns: Callable[[str], Any],
) -> None:
    """Attach ``unread_turns`` summaries for terminal attention lanes in place."""
    for item in attention:
        if not row_is_terminal(item):
            continue
        summaries = compact_unread_turn_summaries(fetch_unread_turns(str(item["id"])))
        if summaries:
            item["unread_turns"] = summaries


def _is_successor_lane(item: dict[str, Any], successors: set[str]) -> bool:
    """A lane the ticker spawned: recorded thread id, or the wire's caller mark
    on the closeout subject when the admit payload carried no thread id."""
    if str(item.get("id") or "") in successors:
        return True
    return _SUCCESSOR_SUBJECT_MARK in str(item.get("last_subject") or "")


def actionable_attention(
    attention: Any, *, state: dict[str, Any] | None = None
) -> list[Any]:
    """Wake items: unread live lanes once per ``turns`` mark (``state.served_lanes``),
    plus each finished *work* lane's closeout once.

    A closeout is the moment the house needs a seat (harvest → fold → decide →
    dispatch), so a terminal lane with unread turns wakes — once per served
    high-water ``turn_number`` (``state.served_closeouts``), ignoring
    post-closeout relay / WAKE / progress turns from ``cursor-auto``, and never
    for a successor the ticker itself spawned (``state.successor_threads`` /
    ``caller=liaison-ticker``). Budget estimates are never attention.

    Attention rows may carry ``unread_turns`` (``turn_number``, ``from``,
    ``subject``, ``body``) for kind discrimination; absent that list the gate
    fails closed (wake) so a missed directive is never suppressed. Live lanes
    without a parseable ``turns`` fail closed the same way.
    """
    st = state or {}
    served = st.get("served_closeouts") or {}
    served_lanes = st.get("served_lanes") or {}
    successors = {str(x) for x in (st.get("successor_threads") or [])}
    items = attention if isinstance(attention, list) else []
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict) or item.get("kind") == "budget_estimate":
            continue
        if row_is_terminal(item):
            if str(item.get("lifecycle") or "").lower() == "abandoned":
                continue
            lane_id = str(item.get("id") or "")
            if int(item.get("unread") or 0) <= 0 or _is_successor_lane(
                item, successors
            ):
                continue
            if not _terminal_lane_has_actionable_unread(
                item, served_mark=_served_mark(served, lane_id)
            ):
                continue
        elif not _live_lane_actionable(item, served_lanes):
            continue
        out.append(item)
    return out


def remint_cap_wall(state: dict[str, Any], night_id: str) -> bool:
    """True while GIW has refused this night's work_key with ``REMINT_CAP``.

    The wire's per-work_key cap is a hard wall for the rest of the night — every
    retry is another 409 plus an admitted turn and an orphaned lane on the root.
    The ticker holds until the night (and the work_key) rolls, instead of
    polling the wall every 120 s (10479, 08:42Z–14:28Z 2026-09-13).
    """
    wall = state.get("remint_cap_wall") or {}
    return str(wall.get("night_id") or "") == night_id


def record_remint_cap(
    state: dict[str, Any], payload: Any, *, night_id: str, at: str
) -> dict[str, Any] | None:
    """Latch a ``REMINT_CAP`` refusal for ``night_id``; returns the wall record
    the first time it is seen tonight, else ``None`` (no repeat page)."""
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if code != _REMINT_CAP_CODE:
        return None
    if remint_cap_wall(state, night_id):
        return None
    wall = {
        "night_id": night_id,
        "at": at,
        "message": str(err.get("message") or "")[:200],
    }
    state["remint_cap_wall"] = wall
    return wall


def handoff_wake(state: dict[str, Any]) -> bool:
    """``go under`` arms exactly one wake: ``handoff.seq`` not yet spawned for."""
    seq = int((state.get("handoff") or {}).get("seq") or 0)
    return seq > 0 and seq != int(state.get("handoff_spawned_seq") or 0)


def idle_ide_forfeit(
    lock: dict[str, Any],
    *,
    register: str,
    policy: dict[str, Any],
    idle_of: Callable[[dict[str, Any]], float | None] | None = None,
    transcripts_dir: Path | None = None,
) -> dict[str, Any] | None:
    """An ``ide:`` seat whose tab stopped writing while the house is autonomous.

    The lease itself is honoured while the operator steers (attended register);
    under ``autonomous`` a silent tab is a stopped liaison (10479, 2026-09-13:
    ``ide:ccd52168…`` claimed at 06:13Z, ``tick_seq=0``, ticker held 90 min on
    ``seat_lock_free``). Returns ``{holder, idle_s}`` when the ticker may release
    it and spawn; ``None`` otherwise (including unmeasurable legacy ``ide:<root>``).
    """
    holder = str(lock.get("holder") or "")
    if register != "autonomous" or not holder.startswith("ide:"):
        return None
    root = transcripts_dir or AGENT_TRANSCRIPTS
    if not ide_transcript_probe_resolved(lock, transcripts_dir=root):
        return None

    def _default_idle(seat_lock: dict[str, Any]) -> float | None:
        return ide_holder_idle_s(seat_lock, transcripts_dir=root)

    idle_s = (idle_of or _default_idle)(lock)
    limit = float(policy.get("ide_idle_forfeit_s") or IDE_IDLE_FORFEIT_S)
    if idle_s is None or idle_s <= limit:
        return None
    return {"holder": holder, "idle_s": round(idle_s)}


def dead_sdk_holder(
    lock: dict[str, Any], *, finished_execution_id: str | None, rows: Any
) -> str | None:
    """An ``sdk:`` seat whose dispatch is over.

    Dead when the pending spawn that just went terminal is the holder (the
    successor claims ``sdk:<execution_id>`` or a prefix of it), or when a
    finished lane's closeout subject names the holder's id. 10599 (2026-09-13)
    closed without ``--release`` and its lease would have held the house for 90
    minutes; the operator's tab had to ``--release`` it by hand.
    """
    holder = str(lock.get("holder") or "")
    ident = holder.split(":", 1)[1] if holder.startswith("sdk:") else ""
    if len(ident) < 8:
        return None
    if finished_execution_id and finished_execution_id.startswith(ident):
        return holder
    for row in rows if isinstance(rows, list) else []:
        if (
            isinstance(row, dict)
            and row_is_terminal(row)
            and ident in str(row.get("last_subject") or "")
        ):
            return holder
    return None


def record_spawn_service(
    state: dict[str, Any], attention: Any, *, root_id: str | None = None
) -> None:
    """After a successful fire: latch the handoff, mark the closeouts and the
    friction rows this successor was spawned for, and remember the successor's
    own lane."""
    latch_rows(state, attention, at=datetime.now(UTC).isoformat(timespec="seconds"))
    handoff = state.get("handoff") or {}
    if handoff.get("seq"):
        state["handoff_spawned_seq"] = int(handoff["seq"])
    served = dict(state.get("served_closeouts") or {})
    served_lanes = dict(state.get("served_lanes") or {})
    for item in attention if isinstance(attention, list) else []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        lane_id = str(item["id"])
        if row_is_terminal(item):
            served[lane_id] = max(
                _served_mark(served, lane_id), _closeout_high_water(item)
            )
        else:
            turns = _lane_turns(item)
            if turns is not None:
                served_lanes[lane_id] = max(_served_mark(served_lanes, lane_id), turns)
    state["served_closeouts"] = dict(list(served.items())[-_SERVED_KEEP:])
    state["served_lanes"] = dict(list(served_lanes.items())[-_SERVED_KEEP:])
    thread_id = str((state.get("pending_spawn") or {}).get("thread_id") or "").strip()
    rid = str(root_id or "").strip()
    if thread_id and thread_id != rid:
        kept = [
            str(x)
            for x in (state.get("successor_threads") or [])
            if str(x) != thread_id
        ]
        state["successor_threads"] = (kept + [thread_id])[-_SUCCESSOR_THREADS_KEEP:]


def pending_spawn_terminal(
    pending: dict[str, Any] | None,
    *,
    is_terminal: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """True when there is no live successor mutex.

    Missing callback keeps the conservative default (pending ⇒ not terminal)
    so a caller that forgets the digest checker still will not double-spawn.
    """
    if not pending:
        return True
    if is_terminal is None:
        return False
    return is_terminal(pending)


def _compact_root_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_number": turn.get("turn_number"),
        "from": turn.get("from") or turn.get("from_agent"),
        "subject": turn.get("subject"),
        "body": str(turn.get("body") or "")[:400],
        "created_at": turn.get("created_at"),
    }


def compact_root_turn_summaries(turns: Any) -> list[dict[str, Any]]:
    """Shrink root bus turns for digest pending-spawn lifecycle checks."""
    if not isinstance(turns, list):
        return []
    return [_compact_root_turn(row) for row in turns if isinstance(row, dict)]


def tip_checkpoint_turn_from_turns(turns: Any) -> int | None:
    """Newest CHECKPOINT turn on the continuity root (tip CHECKPOINT ordinal)."""
    if not isinstance(turns, list):
        return None
    candidates = [
        t
        for t in turns
        if isinstance(t, dict)
        and str(t.get("subject") or "").upper().startswith("CHECKPOINT")
    ]
    if not candidates:
        return None
    tip = max(candidates, key=lambda t: int(t.get("turn_number") or 0))
    num = tip.get("turn_number")
    return int(num) if num is not None else None


def root_turn_surface(raw_turns: Any) -> tuple[list[dict[str, Any]], int | None]:
    """Compact root turns plus tip CHECKPOINT ordinal for digest surfaces."""
    return compact_root_turn_summaries(raw_turns), tip_checkpoint_turn_from_turns(
        raw_turns
    )


def digest_root_surface(
    client: Any, get: Callable[..., Any], root_id: str, root: dict[str, Any]
) -> tuple[list[dict[str, Any]], int | None]:
    """Fetch compact root turns and tip CHECKPOINT ordinal for digest assembly."""
    if root.get("_error"):
        return [], None
    raw = (get(client, "/turns", thread=root_id, last=20) or {}).get("turns") or []
    return root_turn_surface(raw)


def _turn_text(turn: dict[str, Any]) -> str:
    return " ".join(
        str(turn.get(key) or "") for key in ("subject", "body", "from", "from_agent")
    )


def _successor_terminal_on_root(
    pending: dict[str, Any],
    root_turns: list[dict[str, Any]],
    *,
    spawned_at: float | None,
) -> bool:
    """True when the successor's closeout/CHECKPOINT/STAY landed on the root."""
    execution_id = str(pending.get("execution_id") or "").strip()
    if not execution_id:
        return False
    for turn in root_turns:
        if spawned_at is not None:
            created = _parse_iso_ts(str(turn.get("created_at") or "") or None)
            if created is not None and created <= spawned_at:
                continue
        text = _turn_text(turn)
        if execution_id not in text:
            continue
        if _ROOT_SUCCESSOR_TERMINAL_RE.search(text):
            return True
    return False


class PendingTerminalChecker:
    """Callable pending-spawn terminality with an explicit ``last_reason``."""

    last_reason: str | None = None

    def __init__(
        self,
        *,
        root_id: str,
        rows: dict[str, dict[str, Any]],
        root_turns: list[dict[str, Any]],
        backstop_s: float,
        ts: float,
    ) -> None:
        self._root_id = root_id
        self._rows = rows
        self._root_turns = root_turns
        self._backstop_s = backstop_s
        self._ts = ts

    def __call__(self, pending: dict[str, Any]) -> bool:
        tid = str(pending.get("thread_id") or "").strip()
        spawned = _parse_iso_ts(str(pending.get("spawned_at") or "") or None)
        if tid and tid != self._root_id:
            row = self._rows.get(tid)
            if row is not None:
                if row_is_terminal(row):
                    self.last_reason = "lane_lifecycle"
                    return True
                self.last_reason = "pending_live"
                return False
        elif tid and tid == self._root_id:
            if _successor_terminal_on_root(
                pending, self._root_turns, spawned_at=spawned
            ):
                self.last_reason = "lifecycle"
                return True
            if spawned is not None and (self._ts - spawned) > self._backstop_s:
                self.last_reason = "stale_backstop"
                return True
            self.last_reason = "unresolved_root_pending"
            return False
        elif not tid:
            if _successor_terminal_on_root(
                pending, self._root_turns, spawned_at=spawned
            ):
                self.last_reason = "lifecycle"
                return True
            if spawned is not None and (self._ts - spawned) > self._backstop_s:
                self.last_reason = "stale_backstop"
                return True
            self.last_reason = "unresolved_empty_thread"
            return False
        if _successor_terminal_on_root(pending, self._root_turns, spawned_at=spawned):
            self.last_reason = "lifecycle"
            return True
        if spawned is not None and (self._ts - spawned) > self._backstop_s:
            self.last_reason = "stale_backstop"
            return True
        self.last_reason = "pending_live"
        return False


def digest_pending_is_terminal(
    digest: dict[str, Any],
    *,
    now: float | None = None,
) -> PendingTerminalChecker:
    """Build a checker: pending resolves from lane lifecycle, root evidence, or backstop.

    When ``pending_spawn.thread_id`` is the continuity root (or empty), the lane
    branch is unresolvable — terminality comes from successor closeout evidence
    on ``digest.root.recent_turns``, then an explicit stale backstop
    (``policy.pending_stale_backstop_minutes``, default 180), never
    ``max_hop_minutes``.
    """
    rows: dict[str, dict[str, Any]] = {}
    for bucket in (digest.get("lanes") or []), (digest.get("attention") or []):
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if isinstance(item, dict) and item.get("id") is not None:
                rows[str(item["id"])] = item
    policy = digest.get("policy") or {}
    backstop_s = float(policy.get("pending_stale_backstop_minutes") or 180) * 60.0
    ts = now if now is not None else datetime.now(UTC).timestamp()
    root = digest.get("root") or {}
    root_id = str(root.get("id") or "").strip()
    root_turns = root.get("recent_turns")
    if not isinstance(root_turns, list):
        root_turns = []
    return PendingTerminalChecker(
        root_id=root_id,
        rows=rows,
        root_turns=[t for t in root_turns if isinstance(t, dict)],
        backstop_s=backstop_s,
        ts=ts,
    )


def checkpoint_due_wake(state: dict[str, Any], checkpoint_due: bool) -> bool:
    """``checkpoint_due`` wakes once per ``last_cp_tick`` epoch, not every poll.

    Headless CHECKPOINT can fail to seal (a:33355). Without this latch the
    house mills a new Opus every time the prior successor STAYs and releases.
    """
    if not checkpoint_due:
        return False
    cp_tick = int(state.get("last_cp_tick") or 0)
    attempted = int(state.get("checkpoint_due_spawned_tick") or -1)
    return attempted != cp_tick


__all__ = [
    "IDE_IDLE_FORFEIT_S",
    "PendingTerminalChecker",
    "actionable_kind",
    "actionable_attention",
    "build_attention_lanes",
    "checkpoint_due_wake",
    "compact_root_turn_summaries",
    "compact_unread_turn_summaries",
    "dead_sdk_holder",
    "digest_pending_is_terminal",
    "enrich_terminal_attention_turns",
    "handoff_wake",
    "idle_ide_forfeit",
    "pending_spawn_terminal",
    "record_spawn_service",
    "digest_root_surface",
    "row_is_terminal",
    "tip_checkpoint_turn_from_turns",
]
