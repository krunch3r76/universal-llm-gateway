"""Ledger authority for ``park_for_restart`` rows (steer-restart v1, spec D2/D7).

Park state lives on ``cursor_sdk_dispatches`` — the additive ``park_*`` columns
plus ``record_json.park`` — so it survives the very GIW restart it exists to
enable. A parked row is terminal ``cancelled``: it never runs again under its
own id, the lineage continues through a ``resume_of`` child. These helpers are
the only writers of the park columns; ``cursor_dispatch_ledger`` stays the
schema owner (migration) and this module keeps the row mutations out of a file
already past its SLOC ceiling.

Row-level park state, as projected by ``park_projection``::

    parked   — park_kind set, park_resumed_by NULL, not expired
    resumed  — park_resumed_by set (child dispatch id)
    expired  — record_json.park.expired_at set, no child
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

PARK_KIND_RESTART = "park_for_restart"
_DEFAULT_AUTO_RESUME_TTL_S = 86400
_RECORD_KEY = "park"


def park_auto_resume_ttl_s() -> int:
    """Seconds after ``parked_at`` during which GIW auto-resumes a parked row.

    Manual ``team_dispatch(resume_of=…)`` remains possible until the resume
    retain TTL (14 d); expiry only stops *automatic* re-admission so a row
    parked long ago does not wake against a surprising ``code_version``.
    """
    raw = os.environ.get("CURSOR_SDK_PARK_AUTO_RESUME_TTL_S", "").strip()
    if raw:
        return max(0, int(raw))
    return _DEFAULT_AUTO_RESUME_TTL_S


def _now_dt() -> datetime:
    return datetime.now(UTC)


def _now() -> str:
    return _now_dt().isoformat()


def _record_data(record_json: str | None) -> dict[str, Any]:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ParkRow:
    """Projection of one park-bearing ledger row for resume / status callers."""

    dispatch_id: str
    thread_id: str
    execution_id: str | None
    caller_agent: str | None
    resolved_model: str
    status: str
    terminal_status: str | None
    sdk_agent_id: str | None
    state_root: str | None
    source_ref: str | None
    work_key: str | None
    contract: str | None
    packet_path: str | None
    park_kind: str
    park_intent_id: str | None
    parked_at: str | None
    park_resumed_by: str | None
    park_expires_at: str | None
    record_json: str

    @property
    def record(self) -> dict[str, Any]:
        return _record_data(self.record_json)

    @property
    def park(self) -> dict[str, Any]:
        park = self.record.get(_RECORD_KEY)
        return park if isinstance(park, dict) else {}

    @property
    def expired(self) -> bool:
        if self.park.get("expired_at"):
            return True
        if not self.park_expires_at:
            return False
        return self.park_expires_at <= _now()


_ROW_COLUMNS = (
    "dispatch_id, thread_id, execution_id, caller_agent, resolved_model, status, "
    "terminal_status, sdk_agent_id, state_root, source_ref, work_key, contract, "
    "packet_path, park_kind, park_intent_id, parked_at, park_resumed_by, "
    "park_expires_at, record_json"
)


def _row_to_park(row: Any) -> ParkRow:
    return ParkRow(**{k: row[k] for k in row.keys()})


def load_park_row(*, dispatch_id: str) -> ParkRow | None:
    """Return the park projection when the row carries ``park_kind``."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        row = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=? AND park_kind IS NOT NULL",
            (dispatch_id,),
        ).fetchone()
    return _row_to_park(row) if row is not None else None


def mark_parked(
    *,
    dispatch_id: str,
    intent_id: str | None,
    drain_epoch: int | None,
    actor: str,
    reason: str,
    requested_at: str,
    method: str,
    tool_call_count: int,
    last_tool_calls: list[dict[str, str]],
    sidecar_uri: str | None,
    preamble_version: int = 1,
) -> ParkRow | None:
    """Transition a running row to terminal ``cancelled`` + park columns atomically.

    Also stamps ``record_json.resume_retain`` so the Lane-B tree and dispatch
    HOME survive prune until the resume-retain TTL — the same key designed
    stops use. Returns the projection, or ``None`` when the row is gone.
    """
    parked_at = _now()
    expires_at = (_now_dt() + timedelta(seconds=park_auto_resume_ttl_s())).isoformat()
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if row is None:
            return None
        data = _record_data(row["record_json"])
        data[_RECORD_KEY] = {
            "intent_id": intent_id,
            "drain_epoch": drain_epoch,
            "actor": actor,
            "reason": reason,
            "requested_at": requested_at,
            "parked_at": parked_at,
            "method": method,
            "tool_call_count": tool_call_count,
            "last_tool_calls": list(last_tool_calls)[-3:],
            "sidecar_uri": sidecar_uri,
            "preamble_version": preamble_version,
            "resume_attempts": 0,
            "resume_refusals": [],
        }
        data["resume_retain"] = True
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET status='cancelled', "
            "terminal_status='cancelled', terminal_at=?, park_kind=?, "
            "park_intent_id=?, parked_at=?, park_expires_at=?, record_json=? "
            "WHERE dispatch_id=?",
            (
                parked_at,
                PARK_KIND_RESTART,
                intent_id,
                parked_at,
                expires_at,
                _dump(data),
                dispatch_id,
            ),
        )
    return load_park_row(dispatch_id=dispatch_id)


def open_park_rows() -> list[ParkRow]:
    """Park rows with no child yet, oldest ``parked_at`` first (includes expired).

    Callers split expired from live via ``ParkRow.expired`` so the expiry event
    fires exactly once (``mark_park_expired`` stamps ``expired_at``).
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        rows = conn.execute(
            f"SELECT {_ROW_COLUMNS} FROM cursor_sdk_dispatches "
            "WHERE park_kind=? AND park_resumed_by IS NULL "
            "AND status IN ('completed','failed','cancelled') "
            "ORDER BY parked_at ASC, rowid ASC",
            (PARK_KIND_RESTART,),
        ).fetchall()
    return [_row_to_park(r) for r in rows]


def mark_park_resumed(*, parent_id: str, child_id: str) -> bool:
    """Close the park: ``park_resumed_by`` names the admitted child. Idempotent."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        updated = conn.execute(
            "UPDATE cursor_sdk_dispatches SET park_resumed_by=? "
            "WHERE dispatch_id=? AND park_kind IS NOT NULL "
            "AND park_resumed_by IS NULL",
            (child_id, parent_id),
        )
        return updated.rowcount == 1


def record_resume_refusal(*, parent_id: str, reason: str) -> int:
    """Append a refusal to ``record_json.park.resume_refusals``; return attempt count.

    Does not count an attempt — ``bump_resume_attempt`` owns that so a refused
    admission (bumped before the admit) is not double-counted.
    """
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        if row is None:
            return 0
        data = _record_data(row["record_json"])
        park = data.get(_RECORD_KEY)
        if not isinstance(park, dict):
            park = {}
        attempts = int(park.get("resume_attempts") or 0)
        refusals = list(park.get("resume_refusals") or [])
        refusals.append({"reason": reason, "at": _now(), "attempt": attempts})
        park["resume_refusals"] = refusals[-10:]
        data[_RECORD_KEY] = park
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (_dump(data), parent_id),
        )
    return attempts


def bump_resume_attempt(*, parent_id: str) -> int:
    """Count one auto-resume admission attempt; return the new attempt number."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        if row is None:
            return 0
        data = _record_data(row["record_json"])
        park = data.get(_RECORD_KEY)
        if not isinstance(park, dict):
            park = {}
        attempts = int(park.get("resume_attempts") or 0) + 1
        park["resume_attempts"] = attempts
        data[_RECORD_KEY] = park
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (_dump(data), parent_id),
        )
    return attempts


def mark_park_expired(*, parent_id: str) -> bool:
    """Stamp ``record_json.park.expired_at`` once; row columns are untouched."""
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (parent_id,),
        ).fetchone()
        if row is None:
            return False
        data = _record_data(row["record_json"])
        park = data.get(_RECORD_KEY)
        if not isinstance(park, dict):
            park = {}
        if park.get("expired_at"):
            return False
        park["expired_at"] = _now()
        data[_RECORD_KEY] = park
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (_dump(data), parent_id),
        )
    return True


def park_projection(row: ParkRow | None) -> dict[str, Any] | None:
    """Operator-facing ``park`` block for ``dispatch-status`` (None when unparked)."""
    if row is None:
        return None
    if row.park_resumed_by:
        state = "resumed"
    elif row.expired:
        state = "expired"
    else:
        state = "parked"
    park = row.park
    return {
        "state": state,
        "park_kind": row.park_kind,
        "intent_id": row.park_intent_id,
        "parked_at": row.parked_at,
        "park_expires_at": row.park_expires_at,
        "park_resumed_by": row.park_resumed_by,
        "method": park.get("method"),
        "tool_call_count": park.get("tool_call_count"),
        "sidecar_uri": park.get("sidecar_uri"),
        "resume_attempts": park.get("resume_attempts", 0),
        "resume_refusals": park.get("resume_refusals", []),
        "expired_at": park.get("expired_at"),
    }


__all__ = [
    "PARK_KIND_RESTART",
    "ParkRow",
    "bump_resume_attempt",
    "load_park_row",
    "mark_park_expired",
    "mark_park_resumed",
    "mark_parked",
    "open_park_rows",
    "park_auto_resume_ttl_s",
    "park_projection",
    "record_resume_refusal",
]
