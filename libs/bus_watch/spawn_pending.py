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

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_bus_store.sdk_liveness import (
    LivenessVerdict,
    ProbeResult,
    _worker_base_url,
    classify_probe,
    probe_dispatch_status,
)

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
ROOT_SUCCESSOR_TERMINAL_RE = re.compile(
    r"CHECKPOINT|CLOSEOUT|\bSTAY\b|TYPE:\s*(CHECKPOINT|CLOSEOUT|STAY)",
    re.I,
)
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_EXECUTION_IDS_CAP = 8
_PROBE_TIMEOUT_S = 2.0


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


_SUBJECT_CHARS = 56
LAND_RESULT_RE = re.compile(r"\bG\d+\s+LAND\b", re.I)


def attention_now_lane(digest: dict[str, Any]) -> dict[str, Any] | None:
    """Newest non-terminal ``sub_mission`` lane in digest attention (11693#4)."""
    root_id = str((digest.get("root") or {}).get("id") or "").strip()
    candidates: list[tuple[str, dict[str, Any]]] = []
    for item in digest.get("attention") or []:
        if not isinstance(item, dict) or "id" not in item:
            continue
        if root_id and str(item.get("id")) == root_id:
            continue
        if item.get("kind") in ("friction", "budget_estimate"):
            continue
        if item.get("lane_role") != "sub_mission":
            continue
        subject = str(item.get("last_subject") or "")
        if (
            row_is_terminal(item)
            or item.get("terminal")
            or LAND_RESULT_RE.search(subject)
        ):
            continue
        updated = str(item.get("updated_at") or "")
        candidates.append((updated, item))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def attention_now_row(digest: dict[str, Any]) -> str:
    """Formatted NOW row from ``attention_now_lane``."""
    lane = attention_now_lane(digest)
    if lane is None:
        return ""
    subject = str(lane.get("last_subject") or "")[:_SUBJECT_CHARS]
    lane_id = lane["id"]
    if subject:
        return f"agent-bus:{lane_id} · «{subject}»"
    return f"agent-bus:{lane_id}"


def row_is_terminal(row: dict[str, Any]) -> bool:
    """True when a digest lane or attention item is a finished worker.

    Uses ``status`` / ``lifecycle`` only. Subject-regex ``terminal`` is a
    CLOSEOUT/CHECKPOINT hint and can fire on a still-running seat.
    """
    if str(row.get("status") or "") == "closed":
        return True
    return str(row.get("lifecycle") or "").lower() in _TERMINAL_LIFECYCLES


def observe_terminal_lane_closeouts(
    parent_root: str,
    lanes: list[dict[str, Any]],
    state: dict[str, Any],
    client: Any,
    *,
    fetch_turns: Callable[[str], Any],
) -> list[dict[str, Any]]:
    """At ``row_is_terminal`` transition, post one parent-root closeout row."""
    from bus_watch.lane_closeout import observe_terminal_lane_closeouts as _emit

    return _emit(
        parent_root,
        lanes,
        state,
        client,
        fetch_turns=fetch_turns,
    )


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


def _extract_execution_ids(*parts: str, cap: int = _EXECUTION_IDS_CAP) -> list[str]:
    """Stable UUID tokens from full turn text before body compaction."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for match in _UUID_RE.finditer(str(part or "")):
            token = match.group(0).lower()
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
            if len(out) >= cap:
                return out
    return out


def _probe_dispatch_by_id(dispatch_id: str) -> ProbeResult:
    """HTTP GET dispatch-status keyed by ``dispatch_id`` (GIW admin surface)."""
    base = _worker_base_url()
    query = urllib.parse.urlencode({"dispatch_id": dispatch_id})
    url = f"{base}/api/v1/git/admin/dispatch-status?{query}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
            http_status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ProbeResult(payload=None, http_status=404, error=None)
        return ProbeResult(
            payload=None,
            http_status=exc.code,
            error=f"http_error_{exc.code}",
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        return ProbeResult(
            payload=None, http_status=None, error=f"probe_unreachable:{exc}"
        )

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ProbeResult(
            payload=None, http_status=http_status, error="malformed_json"
        )
    if not isinstance(payload, dict):
        return ProbeResult(
            payload=None, http_status=http_status, error="malformed_json"
        )
    return ProbeResult(payload=payload, http_status=http_status, error=None)


def execution_gone(
    pending: dict[str, Any],
    *,
    root_id: str,
    probe_by_thread: Callable[[str], ProbeResult] | None = None,
    probe_by_id: Callable[[str], ProbeResult] | None = None,
) -> bool:
    """True when GIW says the pending execution is gone (fail-closed on probe error)."""
    execution_id = str(pending.get("execution_id") or "").strip()
    if not execution_id:
        return False
    thread_probe = probe_by_thread or probe_dispatch_status
    id_probe = probe_by_id or _probe_dispatch_by_id
    thread_id = str(pending.get("thread_id") or root_id or "").strip()

    if thread_id:
        probe = thread_probe(thread_id)
        if probe.error is None:
            verdict, _, _ = classify_probe(probe, link_execution_id=execution_id)
            if verdict is LivenessVerdict.SKIP_LIVE:
                return False
            if verdict in (
                LivenessVerdict.ALLOW_ORPHAN,
                LivenessVerdict.TERMINAL_BACKFILL,
            ):
                return True
            if verdict is not LivenessVerdict.DEFER:
                return False

    probe = id_probe(execution_id)
    if probe.error is not None:
        return False
    if probe.http_status == 404:
        return True
    payload = probe.payload
    if payload is None:
        return False
    if payload.get("status") is None:
        return True
    verdict, _, _ = classify_probe(probe, link_execution_id=execution_id)
    return verdict in (LivenessVerdict.ALLOW_ORPHAN, LivenessVerdict.TERMINAL_BACKFILL)


def _compact_root_turn(
    turn: dict[str, Any], *, thread: str | None = None
) -> dict[str, Any]:
    subject = str(turn.get("subject") or "")
    body = str(turn.get("body") or "")
    return {
        "turn_number": turn.get("turn_number"),
        "from": turn.get("from") or turn.get("from_agent"),
        "subject": subject,
        "body": body[:400],
        "execution_ids": _extract_execution_ids(subject, body),
        "created_at": turn.get("created_at"),
        "read_at": turn.get("read_at"),
        "status": turn.get("status"),
        "thread": str(turn.get("thread") or thread or ""),
    }


def compact_root_turn_summaries(
    turns: Any, *, thread: str | None = None
) -> list[dict[str, Any]]:
    """Shrink root bus turns for digest pending-spawn lifecycle checks."""
    if not isinstance(turns, list):
        return []
    return [
        _compact_root_turn(row, thread=thread) for row in turns if isinstance(row, dict)
    ]


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


def root_turn_surface(
    raw_turns: Any, *, thread: str | None = None
) -> tuple[list[dict[str, Any]], int | None]:
    """Compact root turns plus tip CHECKPOINT ordinal for digest surfaces."""
    return compact_root_turn_summaries(raw_turns, thread=thread), (
        tip_checkpoint_turn_from_turns(raw_turns)
    )


def digest_root_surface(
    client: Any, get: Callable[..., Any], root_id: str, root: dict[str, Any]
) -> tuple[list[dict[str, Any]], int | None, list[dict[str, Any]]]:
    """Fetch compact root turns, tip CHECKPOINT ordinal, and unread judgment feed."""
    if root.get("_error"):
        return [], None, []
    raw = (get(client, "/turns", thread=root_id, last=20) or {}).get("turns") or []
    recent_turns, tip_cp = root_turn_surface(raw, thread=root_id)
    unread_raw = (
        get(client, "/turns", thread=root_id, unread=True, last=25) or {}
    ).get("turns") or []
    unread_turns = compact_root_turn_summaries(unread_raw, thread=root_id)
    if not unread_turns:
        fallback = (get(client, "/turns", thread=root_id, last=25) or {}).get(
            "turns"
        ) or []
        unread_turns = compact_root_turn_summaries(fallback, thread=root_id)
    return recent_turns, tip_cp, unread_turns


def _turn_text(turn: dict[str, Any]) -> str:
    return " ".join(
        str(turn.get(key) or "") for key in ("subject", "body", "from", "from_agent")
    )


def _pending_execution_id_in_turn(
    pending: dict[str, Any], turn: dict[str, Any]
) -> bool:
    execution_id = str(pending.get("execution_id") or "").strip()
    if not execution_id:
        return False
    ids = turn.get("execution_ids")
    if isinstance(ids, list) and execution_id.lower() in {str(x).lower() for x in ids}:
        return True
    return execution_id in _turn_text(turn)


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
        if not _pending_execution_id_in_turn(pending, turn):
            continue
        text = _turn_text(turn)
        if ROOT_SUCCESSOR_TERMINAL_RE.search(text):
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
        execution_gone_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self._root_id = root_id
        self._rows = rows
        self._root_turns = root_turns
        self._backstop_s = backstop_s
        self._ts = ts
        self._execution_gone = execution_gone_fn or (
            lambda pending: execution_gone(pending, root_id=root_id)
        )

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
            if self._execution_gone(pending):
                self.last_reason = "execution_gone"
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
            if self._execution_gone(pending):
                self.last_reason = "execution_gone"
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
    execution_gone_fn: Callable[[dict[str, Any]], bool] | None = None,
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
        execution_gone_fn=execution_gone_fn,
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
    "LAND_RESULT_RE",
    "PendingTerminalChecker",
    "ROOT_SUCCESSOR_TERMINAL_RE",
    "execution_gone",
    "attention_now_lane",
    "attention_now_row",
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
    "observe_terminal_lane_closeouts",
    "pending_spawn_terminal",
    "record_spawn_service",
    "digest_root_surface",
    "row_is_terminal",
    "tip_checkpoint_turn_from_turns",
]
