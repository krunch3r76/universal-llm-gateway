"""Conductor mission park gate — ledger-authoritative refuse/release (H2)."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from universal_protocol.errors import ProtocolError

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_PARK_REASON_KEY,
    HOP_PARKED_KEY,
)
from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)

HOP_PARK_RELEASED_AT_KEY = "hop_park_released_at"
CONDUCTOR_MISSION_PARKED_CODE = "CONDUCTOR_MISSION_PARKED"


@dataclass(frozen=True, slots=True)
class ParkState:
    parked_dispatch_id: str
    reason: str
    hop_seq: int | None
    parked_at: str


class ConductorMissionParked(Exception):  # noqa: N818 — wire code CONDUCTOR_MISSION_PARKED
    """Raised when a conductor admit targets a mission still parked on the ledger."""

    def __init__(self, *, park_state: ParkState) -> None:
        self.park_state = park_state
        super().__init__(
            f"conductor mission parked dispatch_id={park_state.parked_dispatch_id!r}"
        )

    def to_protocol_error(self) -> ProtocolError:
        ps = self.park_state
        return ProtocolError(
            code=CONDUCTOR_MISSION_PARKED_CODE,
            message="Conductor mission parked; explicit release required",
            source="git_integration_worker",
            retryable=False,
            data={
                "reason": ps.reason,
                "parked_dispatch_id": ps.parked_dispatch_id,
                "hop_seq": ps.hop_seq,
                "release": "generation_options.hop_park_release=true",
            },
        )


def _record_dict(record_json: str | None) -> dict[str, Any]:
    if not record_json:
        return {}
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_parked_row(record_json: str | None) -> bool:
    return _record_dict(record_json).get(HOP_PARKED_KEY) is True


def _park_released(record_json: str | None) -> bool:
    released = _record_dict(record_json).get(HOP_PARK_RELEASED_AT_KEY)
    return released is not None and released != ""


def mission_park_state(
    conn: sqlite3.Connection,
    *,
    work_key: str | None = None,
    thread_id: str | None = None,
) -> ParkState | None:
    """Latest parked terminal row for ``work_key`` or ``thread_id``."""
    clauses: list[str] = []
    params: list[Any] = []
    if work_key:
        clauses.append("work_key=?")
        params.append(work_key)
    if thread_id:
        clauses.append("thread_id=?")
        params.append(thread_id)
    if not clauses:
        return None
    where = " OR ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM cursor_sdk_dispatches "
        f"WHERE ({where}) AND status IN ('completed','failed','cancelled') "
        f"ORDER BY COALESCE(json_extract(record_json, '$.hop_seq'), 0) DESC, "
        f"COALESCE(terminal_at, queued_at) DESC",
        tuple(params),
    ).fetchall()
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if not is_conductor_dispatch_row(mapped):
            continue
        record_json = str(mapped.get("record_json") or "")
        if not _is_parked_row(record_json) or _park_released(record_json):
            continue
        hop_fields = hop_fields_from_record_json(record_json)
        hop_seq = hop_fields.get("hop_seq")
        hop_seq_int = int(hop_seq) if isinstance(hop_seq, int) else None
        reason = str(
            _record_dict(record_json).get(HOP_PARK_REASON_KEY) or "hop_parked"
        )
        parked_at = str(mapped.get("terminal_at") or mapped.get("queued_at") or "")
        dispatch_id = str(mapped.get("dispatch_id") or "")
        if not dispatch_id:
            continue
        return ParkState(
            parked_dispatch_id=dispatch_id,
            reason=reason,
            hop_seq=hop_seq_int,
            parked_at=parked_at,
        )
    return None


def refuse_parked_conductor_mission(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    thread_id: str | None,
    read_only: bool,
    contract: str | None,
) -> None:
    """Raise ``ConductorMissionParked`` when a conductor mission is still parked."""
    if read_only:
        return
    if str(contract or "").lower() != "conductor":
        return
    state = mission_park_state(conn, work_key=work_key, thread_id=thread_id)
    if state is not None:
        raise ConductorMissionParked(park_state=state)


def release_mission_park(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    thread_id: str | None,
    caller_agent: str,
) -> ParkState | None:
    """Clear park hold on the latest parked mission row; return released state."""
    _ = caller_agent
    state = mission_park_state(conn, work_key=work_key, thread_id=thread_id)
    if state is None:
        return None
    row = conn.execute(
        "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (state.parked_dispatch_id,),
    ).fetchone()
    if row is None:
        return None
    data = _record_dict(str(row["record_json"] or ""))
    data[HOP_PARK_RELEASED_AT_KEY] = time.time()
    conn.execute(
        "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
        (
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            state.parked_dispatch_id,
        ),
    )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_park_released,
    )

    emit_frontier_sdk_conductor_hop_park_released(
        parked_dispatch_id=state.parked_dispatch_id,
        thread_id=thread_id or "",
        work_key=work_key,
        caller_agent=caller_agent,
    )
    return state


__all__ = [
    "CONDUCTOR_MISSION_PARKED_CODE",
    "ConductorMissionParked",
    "HOP_PARK_RELEASED_AT_KEY",
    "ParkState",
    "mission_park_state",
    "refuse_parked_conductor_mission",
    "release_mission_park",
]
