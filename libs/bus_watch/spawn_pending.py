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

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from bus_watch.friction_rows import latch_rows
from bus_watch.ide_budget import ide_holder_idle_s

_TERMINAL_LIFECYCLES = frozenset({"completed", "failed", "cancelled", "closed"})
_SUCCESSOR_SUBJECT_MARK = "caller=liaison-ticker"
_SERVED_KEEP = 200
_SUCCESSOR_THREADS_KEEP = 50
IDE_IDLE_FORFEIT_S = 1200.0


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


def _is_successor_lane(item: dict[str, Any], successors: set[str]) -> bool:
    """A lane the ticker spawned: recorded thread id, or the wire's caller mark
    on the closeout subject when the admit payload carried no thread id."""
    if str(item.get("id") or "") in successors:
        return True
    return _SUCCESSOR_SUBJECT_MARK in str(item.get("last_subject") or "")


def actionable_attention(
    attention: Any, *, state: dict[str, Any] | None = None
) -> list[Any]:
    """Wake items: unread live lanes, plus each finished *work* lane's closeout once.

    A closeout is the moment the house needs a seat (harvest → fold → decide →
    dispatch), so a terminal lane with unread turns wakes — once per turn count
    (``state.served_closeouts``) and never for a successor the ticker itself
    spawned (``state.successor_threads`` / ``caller=liaison-ticker``). Budget
    estimates are never attention.
    """
    st = state or {}
    served = st.get("served_closeouts") or {}
    successors = {str(x) for x in (st.get("successor_threads") or [])}
    items = attention if isinstance(attention, list) else []
    out: list[Any] = []
    for item in items:
        if not isinstance(item, dict) or item.get("kind") == "budget_estimate":
            continue
        if row_is_terminal(item):
            if int(item.get("unread") or 0) <= 0 or _is_successor_lane(
                item, successors
            ):
                continue
            if str(served.get(str(item.get("id")))) == str(item.get("turns")):
                continue
        out.append(item)
    return out


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
    idle_s = (idle_of or ide_holder_idle_s)(lock)
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


def record_spawn_service(state: dict[str, Any], attention: Any) -> None:
    """After a successful fire: latch the handoff, mark the closeouts and the
    friction rows this successor was spawned for, and remember the successor's
    own lane."""
    latch_rows(state, attention, at=datetime.now(UTC).isoformat(timespec="seconds"))
    handoff = state.get("handoff") or {}
    if handoff.get("seq"):
        state["handoff_spawned_seq"] = int(handoff["seq"])
    served = dict(state.get("served_closeouts") or {})
    for item in attention if isinstance(attention, list) else []:
        if (
            isinstance(item, dict)
            and row_is_terminal(item)
            and item.get("id") is not None
        ):
            served[str(item["id"])] = item.get("turns")
    state["served_closeouts"] = dict(list(served.items())[-_SERVED_KEEP:])
    thread_id = str((state.get("pending_spawn") or {}).get("thread_id") or "").strip()
    if thread_id:
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


def digest_pending_is_terminal(
    digest: dict[str, Any],
    *,
    now: float | None = None,
) -> Callable[[dict[str, Any]], bool]:
    """Build a checker: pending ``thread_id`` is terminal per digest lanes.

    A worker missing from the digest is still treated as live (admit race)
    unless ``spawned_at`` is older than ``policy.max_hop_minutes``. Vanished
    plus stale is the 10534 failure class when the mutex outlived the seat.
    """
    rows: dict[str, dict[str, Any]] = {}
    for bucket in (digest.get("lanes") or []), (digest.get("attention") or []):
        if not isinstance(bucket, list):
            continue
        for item in bucket:
            if isinstance(item, dict) and item.get("id") is not None:
                rows[str(item["id"])] = item
    max_age_s = float((digest.get("policy") or {}).get("max_hop_minutes") or 60) * 60.0
    ts = now if now is not None else datetime.now(UTC).timestamp()

    def is_terminal(pending: dict[str, Any]) -> bool:
        tid = str(pending.get("thread_id") or "").strip()
        if tid:
            row = rows.get(tid)
            if row is not None:
                return row_is_terminal(row)
        spawned = _parse_iso_ts(str(pending.get("spawned_at") or "") or None)
        if spawned is not None and (ts - spawned) > max_age_s:
            return True
        return False

    return is_terminal


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
    "actionable_attention",
    "checkpoint_due_wake",
    "dead_sdk_holder",
    "digest_pending_is_terminal",
    "handoff_wake",
    "idle_ide_forfeit",
    "pending_spawn_terminal",
    "record_spawn_service",
    "row_is_terminal",
]
