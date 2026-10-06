"""Durable in-flight execution state on the registry row — the one authority.

A cdp_ask execution used to exist only in ``ExecutionStore`` memory. The drain
judged busy from that memory, so a recycle first erased the evidence and then
cut the streams it protected (a:36948 point 3), and a Chrome death with
cdp_ask up left the execution ``failed`` even though the Cowork session it was
harvesting still existed at its ``chat_url`` (a:36969).

This module owns ``row["execution_state"]`` in ``active.json``::

    {execution_id, state, kind, started_at, updated_at, holder_pid, reason?}

    in flight : seated → streaming
    settled   : finished | failed | aborted | awaiting_wake | transferred | expired

It is the authority because it is the only record of *what an execution is
doing* that survives the process: the row already carries the durable
``execution_id ↔ chat_url`` link (``session_address.bind_session_address``);
this field adds the stream state beside it. ``execution_id`` on a row is set
at bind and never cleared (a:36953), so presence is not busy — only a state in
``IN_FLIGHT_STATES`` is.

Projections that must derive from this field, never from process memory:
``cdp_ask.work_projection.drain_projection`` (``busy``), ``dormant_drain
.row_drain_protection`` (``execution_in_flight``), the manage restart witness
(``cdp_ask_inflight_witness``), and ``ExecutionStore`` hydration at boot.

Restart-gate busy (non-force ``sync_restart cdp_ask``) excludes in-flight rows
with ``kind=followup``; hygiene and ``in_flight_rows`` still count them.

Invariant (the pattern another process-local gate can adopt)::

    gate G judges busy(work) ⇒ ∃ durable record R (fold + append log):
      one authority module owns R · R readable by the next process of G ·
      busy(G) := f(R), never f(memory(G)) ·
      transitions(R) emit advisory events (Event Service is not R) ·
      in-flight entries carry started_at ⇒ a TTL settles abandoned rows

Every write goes through ``set_execution_state`` under ``ports.lock`` and
appends an ``execution_state`` line to ``registry.jsonl``; the event is a
mirror, not the record.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import Any, Literal

from claude_bundles import cdp_registry_store as _store

ExecutionState = Literal[
    "seated",
    "streaming",
    "finished",
    "failed",
    "aborted",
    "awaiting_wake",
    "transferred",
    "expired",
]
ExecutionKind = Literal["execution", "followup"]

FIELD = "execution_state"
IN_FLIGHT_STATES: frozenset[str] = frozenset({"seated", "streaming"})
SETTLED_STATES: frozenset[str] = frozenset(
    {"finished", "failed", "aborted", "awaiting_wake", "transferred", "expired"}
)
# Matches ExecutionStore.execution_ttl_s: past this age an in-flight entry
# whose process never settled it reads as expired rather than gating forever.
EXECUTION_IN_FLIGHT_TTL_S = 7200.0
# A followup paste is a turn nobody harvests through the store; the seat is
# busy while Cowork streams the reply. Bounded like the operator idle grace.
FOLLOWUP_IN_FLIGHT_TTL_S = 1800.0

ExecutionFreshness = Literal["live", "expired_by_ttl"]

__all__ = [
    "EXECUTION_IN_FLIGHT_TTL_S",
    "FIELD",
    "FOLLOWUP_IN_FLIGHT_TTL_S",
    "IN_FLIGHT_STATES",
    "SETTLED_STATES",
    "ExecutionFreshness",
    "ExecutionKind",
    "ExecutionState",
    "execution_state_for_execution_id",
    "execution_state_of",
    "expire_stale_in_flight",
    "in_flight_entry_contributes_restart_gate_busy",
    "in_flight_rows",
    "in_flight_rows_for_restart_gate",
    "row_execution_in_flight",
    "row_for_execution_id",
    "set_execution_state",
]


def row_for_execution_id(
    active: dict[str, dict[str, Any]], execution_id: str
) -> tuple[str, dict[str, Any]] | None:
    """Return ``(registration_id, row)`` when *execution_id* matches a row field."""
    eid = str(execution_id or "").strip()
    if not eid:
        return None
    for rid, row in active.items():
        if not isinstance(row, dict):
            continue
        entry = execution_state_of(row)
        if entry is not None and str(entry.get("execution_id") or "") == eid:
            return rid, row
    return None


def execution_state_for_execution_id(
    execution_id: str,
    *,
    now: float | None = None,
    active: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any], ExecutionFreshness] | None:
    """Look up authority ``execution_state`` by execution id.

    Returns ``(registration_id, entry, freshness)`` or ``None`` when absent.
    TTL-expired in-flight entries report ``freshness=expired_by_ttl`` with the
    raw entry still attached so callers can render ``expired`` honestly.
    """
    eid = str(execution_id or "").strip()
    if not eid:
        return None
    if active is None:
        active = _store.load_active()
    match = row_for_execution_id(active, eid)
    if match is None:
        return None
    rid, row = match
    entry = execution_state_of(row)
    if entry is None:
        return None
    ts = time.time() if now is None else now
    if entry["state"] in IN_FLIGHT_STATES:
        started = entry.get("started_at")
        if isinstance(started, (int, float)) and ts - float(started) >= _ttl_for(entry):
            return rid, entry, "expired_by_ttl"
    return rid, entry, "live"


def execution_state_of(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return the row's ``execution_state`` entry when it is well-formed."""
    entry = row.get(FIELD)
    if not isinstance(entry, dict):
        return None
    if not str(entry.get("execution_id") or "").strip():
        return None
    if str(entry.get("state") or "") not in IN_FLIGHT_STATES | SETTLED_STATES:
        return None
    return entry


def _ttl_for(entry: dict[str, Any]) -> float:
    return (
        FOLLOWUP_IN_FLIGHT_TTL_S
        if entry.get("kind") == "followup"
        else EXECUTION_IN_FLIGHT_TTL_S
    )


def row_execution_in_flight(
    row: dict[str, Any], *, now: float | None = None
) -> dict[str, Any] | None:
    """Return the in-flight entry on *row*, or None when settled, absent, or expired.

    Expiry is read-side: a later process must not wait for the writer that
    abandoned the entry to come back and settle it.
    """
    entry = execution_state_of(row)
    if entry is None or entry["state"] not in IN_FLIGHT_STATES:
        return None
    started = entry.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    ts = time.time() if now is None else now
    if ts - float(started) >= _ttl_for(entry):
        return None
    return entry


def in_flight_entry_contributes_restart_gate_busy(entry: dict[str, Any]) -> bool:
    """True when an in-flight ``execution_state`` entry blocks cdp_ask restart.

    Followup paste stamps (``kind=followup``) remain in ``in_flight_rows`` for
    hygiene but must not defer non-force recycle when no generate/ask work exists.
    """
    return str(entry.get("kind") or "execution") != "followup"


def in_flight_rows(
    active: dict[str, dict[str, Any]], *, now: float | None = None
) -> dict[str, dict[str, Any]]:
    """Registry rows whose recorded execution is in flight, keyed by registration_id."""
    ts = time.time() if now is None else now
    return {
        rid: row
        for rid, row in active.items()
        if isinstance(row, dict) and row_execution_in_flight(row, now=ts) is not None
    }


def in_flight_rows_for_restart_gate(
    active: dict[str, dict[str, Any]], *, now: float | None = None
) -> dict[str, dict[str, Any]]:
    """In-flight rows that defer cdp_ask non-force restart (excludes ``followup``)."""
    ts = time.time() if now is None else now
    out: dict[str, dict[str, Any]] = {}
    for rid, row in in_flight_rows(active, now=ts).items():
        entry = row_execution_in_flight(row, now=ts)
        if entry is not None and in_flight_entry_contributes_restart_gate_busy(entry):
            out[rid] = row
    return out


def set_execution_state(
    registration_id: str,
    *,
    execution_id: str,
    state: ExecutionState,
    kind: ExecutionKind = "execution",
    reason: str | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Stamp *state* for *execution_id* on the row; return the entry written.

    Returns None when the row is gone. A repeat of the same state and reason
    for the same execution writes nothing; the same state with a new reason
    (``resumed:boot`` on a row a predecessor left ``streaming``) is a receipt
    and refreshes ``holder_pid``. A different execution replaces the entry and
    restarts ``started_at`` — one row drives one execution at a time. The
    write is one ``active.json`` replace plus one ``execution_state`` journal
    line under ``ports.lock``; the advisory event follows outside the lock.
    """
    rid = str(registration_id or "").strip()
    eid = str(execution_id or "").strip()
    if not rid or not eid:
        return None
    ts = time.time() if now is None else now
    entry: dict[str, Any] | None = None
    previous: str | None = None
    with _store.ports_lock():
        active = _store.load_active()
        row = active.get(rid)
        if row is None:
            return None
        current = execution_state_of(row)
        if (
            kind == "followup"
            and current is not None
            and str(current.get("execution_id") or "") != eid
            and current["state"] in IN_FLIGHT_STATES
            and str(current.get("kind") or "execution") != "followup"
        ):
            return current
        if current is not None and current["execution_id"] == eid:
            previous = str(current["state"])
            if previous == state and (reason or None) == current.get("reason"):
                return current
            started = current.get("started_at")
        else:
            started = None
        entry = {
            "execution_id": eid,
            "state": state,
            "kind": kind,
            "started_at": float(started) if isinstance(started, (int, float)) else ts,
            "updated_at": ts,
            "holder_pid": os.getpid(),
        }
        if reason:
            entry["reason"] = str(reason)
        updated = dict(row)
        updated[FIELD] = entry
        active[rid] = updated
        _store.write_active(active)
        _store.append_log(
            "execution_state",
            {"registration_id": rid, "previous_state": previous, **entry},
        )
    _emit_state_changed(rid, entry, previous=previous, chat_url=row.get("chat_url"))
    return entry


def expire_stale_in_flight(*, now: float | None = None) -> list[str]:
    """Settle in-flight entries past their TTL as ``expired``; return execution ids.

    Read-side expiry already ignores them; this write keeps the journal honest
    so a later reader sees an explicit end, not an entry that silently aged out.
    """
    ts = time.time() if now is None else now
    expired: list[tuple[str, dict[str, Any], str | None]] = []
    with _store.ports_lock():
        active = _store.load_active()
        for rid, row in active.items():
            if not isinstance(row, dict):
                continue
            entry = execution_state_of(row)
            if entry is None or entry["state"] not in IN_FLIGHT_STATES:
                continue
            started = entry.get("started_at")
            if not isinstance(started, (int, float)):
                continue
            if ts - float(started) < _ttl_for(entry):
                continue
            settled = {**entry, "state": "expired", "updated_at": ts, "reason": "ttl"}
            updated = dict(row)
            updated[FIELD] = settled
            active[rid] = updated
            _store.append_log(
                "execution_state",
                {
                    "registration_id": rid,
                    "previous_state": entry["state"],
                    **settled,
                },
            )
            expired.append((rid, settled, row.get("chat_url")))
        if expired:
            _store.write_active(active)
    for rid, settled, chat_url in expired:
        _emit_state_changed(rid, settled, previous=None, chat_url=chat_url)
    return [str(settled["execution_id"]) for _rid, settled, _url in expired]


def _emit_state_changed(
    registration_id: str,
    entry: dict[str, Any],
    *,
    previous: str | None,
    chat_url: Any,
) -> None:
    from claude_bundles import cdp_registry_events as _events

    with contextlib.suppress(Exception):
        _events.emit(
            _events.cdp_execution_state_changed(
                registration_id=registration_id,
                execution_id=str(entry["execution_id"]),
                state=str(entry["state"]),
                previous_state=previous,
                kind=str(entry.get("kind") or "execution"),
                reason=entry.get("reason"),
                chat_url=str(chat_url) if chat_url else None,
            )
        )
