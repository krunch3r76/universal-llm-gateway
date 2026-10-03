"""Conductor mission park gate — one open-park rule for refuse, release, and census."""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger
from universal_protocol.errors import ProtocolError

from services.git_integration_worker.cursor_sdk_closeout.conductor_hop_budget import (
    HOP_MISSION_CAP_RELEASE_BASELINE_KEY,
    HOP_PARK_REASON_KEY,
    HOP_PARKED_KEY,
    PARK_REASON_MISSION_CAP,
    count_hop_attempts,
    list_mission_terminal_chain,
)
from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)

HOP_PARK_RELEASED_AT_KEY = "hop_park_released_at"
CONDUCTOR_MISSION_PARKED_CODE = "CONDUCTOR_MISSION_PARKED"
# a:37748: a producer-harvest admit releases only this park, inside the admit
# transaction, and only on the hop_from row. Other budget reasons stay put.
_HOP_REASON_PRODUCER_HARVEST = "producer_harvest"
_PARK_REASON_NO_PROGRESS_CAP = "hop_budget_no_progress_cap"

PARK_KINDS: frozenset[str] = frozenset({"budget", "restart"})


def open_restart_park_sql(table_alias: str = "") -> str:
    """Open ``park_for_restart`` predicate; *table_alias* is the dispatch-row alias.

    Child match is ``_existing_child``: only ``admitted_via`` park-resume
    children close the park (a:37671, a:37672). Unaliased SQL qualifies the
    outer table name so the ``NOT EXISTS`` subquery cannot bind ``dispatch_id``
    to ``park_resume_child``.
    """
    from services.git_integration_worker.cursor_sdk_park_resume import (
        ADMITTED_VIA_PARK_RESUME,
    )

    col = f"{table_alias}." if table_alias else ""
    parent_id = (
        f"{table_alias}.dispatch_id"
        if table_alias
        else ("cursor_sdk_dispatches.dispatch_id")
    )
    like = '%"admitted_via":"' + ADMITTED_VIA_PARK_RESUME + '"%'
    return (
        f"({col}park_kind='park_for_restart' AND {col}park_resumed_by IS NULL "
        f"AND ({col}park_expires_at IS NULL OR {col}park_expires_at > ?) "
        f"AND NOT EXISTS ("
        f"SELECT 1 FROM cursor_sdk_dispatches AS park_resume_child "
        f"WHERE park_resume_child.resume_of = {parent_id} "
        f"AND park_resume_child.record_json LIKE '{like}'"
        f"))"
    )


OPEN_RESTART_PARK_SQL = open_restart_park_sql()

_LOG = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ParkState:
    parked_dispatch_id: str
    reason: str
    hop_seq: int | None
    parked_at: str


@dataclass(frozen=True, slots=True)
class OpenPark:
    dispatch_id: str
    kind: str
    work_key: str | None
    thread_id: str | None
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


def _record_is_partial(raw: Any) -> bool:
    if raw is None:
        return False
    text = str(raw)
    if not text.strip():
        return False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return True
    return not isinstance(data, dict)


def _park_released(record_json: str | None) -> bool:
    released = _record_dict(record_json).get(HOP_PARK_RELEASED_AT_KEY)
    return released is not None and released != ""


def _iso_now(now: str | None) -> str:
    if now is not None:
        return now
    return datetime.now(UTC).isoformat()


def _budget_open_parks(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    thread_id: str | None,
) -> list[OpenPark]:
    clauses: list[str] = []
    params: list[Any] = []
    if work_key:
        clauses.append("work_key=?")
        params.append(work_key)
    if thread_id:
        clauses.append("thread_id=?")
        params.append(thread_id)
    if not clauses:
        return []
    where = " OR ".join(clauses)
    rows = conn.execute(
        f"SELECT * FROM cursor_sdk_dispatches "
        f"WHERE ({where}) AND status IN ('completed','failed','cancelled') "
        f"AND CASE WHEN json_valid(record_json) "
        f"THEN json_extract(record_json, '$.hop_parked') END = 1",
        tuple(params),
    ).fetchall()
    parks: list[OpenPark] = []
    for raw in rows:
        mapped = {k: raw[k] for k in raw.keys()}
        record_json = str(mapped.get("record_json") or "")
        if _record_is_partial(record_json):
            continue
        if not is_conductor_dispatch_row(mapped):
            continue
        if _record_dict(record_json).get(HOP_PARKED_KEY) is not True:
            continue
        if _park_released(record_json):
            continue
        hop_fields = hop_fields_from_record_json(record_json)
        hop_seq = hop_fields.get("hop_seq")
        hop_seq_sort = int(hop_seq) if isinstance(hop_seq, int) else 0
        reason = str(_record_dict(record_json).get(HOP_PARK_REASON_KEY) or "hop_parked")
        parked_at = str(mapped.get("terminal_at") or mapped.get("queued_at") or "")
        dispatch_id = str(mapped.get("dispatch_id") or "")
        if not dispatch_id:
            continue
        parks.append(
            (
                hop_seq_sort,
                parked_at,
                OpenPark(
                    dispatch_id=dispatch_id,
                    kind="budget",
                    work_key=str(mapped.get("work_key") or "") or None,
                    thread_id=str(mapped.get("thread_id") or "") or None,
                    reason=reason,
                    hop_seq=int(hop_seq) if isinstance(hop_seq, int) else None,
                    parked_at=parked_at,
                ),
            )
        )
    parks.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [t[2] for t in parks]


def _restart_open_parks(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    now: str,
) -> list[OpenPark]:
    if not work_key:
        return []
    rows = conn.execute(
        f"SELECT * FROM cursor_sdk_dispatches WHERE work_key=? AND {OPEN_RESTART_PARK_SQL}",
        (work_key, now),
    ).fetchall()
    parks: list[OpenPark] = []
    for raw in rows:
        mapped = {k: raw[k] for k in raw.keys()}
        dispatch_id = str(mapped.get("dispatch_id") or "")
        if not dispatch_id:
            continue
        parks.append(
            OpenPark(
                dispatch_id=dispatch_id,
                kind="restart",
                work_key=str(mapped.get("work_key") or "") or None,
                thread_id=str(mapped.get("thread_id") or "") or None,
                reason="park_for_restart",
                hop_seq=None,
                parked_at=str(
                    mapped.get("parked_at") or mapped.get("terminal_at") or ""
                ),
            )
        )
    return parks


def open_parks(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    thread_id: str | None,
    now: str | None = None,
    kinds: frozenset[str] = PARK_KINDS,
) -> tuple[OpenPark, ...]:
    """Read-only open parks for budget and/or restart kinds in admission scope."""
    if not work_key and not thread_id:
        return ()
    unknown = kinds - PARK_KINDS
    if unknown:
        raise ValueError(f"unknown park kinds: {sorted(unknown)}")
    iso = _iso_now(now)
    out: list[OpenPark] = []
    if "budget" in kinds:
        out.extend(_budget_open_parks(conn, work_key=work_key, thread_id=thread_id))
    if "restart" in kinds:
        out.extend(_restart_open_parks(conn, work_key=work_key, now=iso))
    return tuple(out)


def mission_park_state(
    conn: sqlite3.Connection,
    *,
    work_key: str | None = None,
    thread_id: str | None = None,
) -> ParkState | None:
    """First open budget park in scope (same ordering as ``open_parks``)."""
    parks = open_parks(
        conn, work_key=work_key, thread_id=thread_id, kinds=frozenset({"budget"})
    )
    if not parks:
        return None
    p = parks[0]
    return ParkState(
        parked_dispatch_id=p.dispatch_id,
        reason=p.reason,
        hop_seq=p.hop_seq,
        parked_at=p.parked_at,
    )


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


def _stamp_mission_cap_baseline_on_latest_terminal(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
) -> None:
    """Record the mission-cap window on the latest terminal hop for *work_key*.

    Used when an admit carries ``hop_park_release`` and no budget park is open.
    The UPDATE shares *conn* with the admit so the stamp commits with it.
    """
    key = str(work_key or "").strip()
    if not key:
        return
    chain = list_mission_terminal_chain(work_key=key, conn=conn)
    if not chain:
        return
    target = chain[-1]
    dispatch_id = str(target.get("dispatch_id") or "")
    if not dispatch_id:
        return
    raw = str(target.get("record_json") or "")
    if _record_is_partial(raw):
        return
    data = _record_dict(raw)
    data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] = count_hop_attempts(chain)
    conn.execute(
        "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
        (
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            dispatch_id,
        ),
    )


def release_linked_no_progress_park(
    conn: sqlite3.Connection,
    *,
    hop_from: str | None,
    hop_reason: str | None,
    thread_id: str | None,
    work_key: str | None,
    caller_agent: str,
    post_commit_emits: list[Callable[[], None]],
) -> bool:
    """Release the hop_from no-progress park before ``refuse_parked_conductor_mission``.

    Runs on the admit connection, so a later exception rolls the stamp back.
    A mission-cap, crash-cap, or admit-retry row is left open. ``hop_park_release``
    stays unset; the park-released event is queued on *post_commit_emits*.
    """
    if str(hop_reason or "") != _HOP_REASON_PRODUCER_HARVEST:
        return False
    pred = str(hop_from or "").strip()
    if not pred:
        return False
    row = conn.execute(
        "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (pred,),
    ).fetchone()
    if row is None:
        return False
    raw = str(row["record_json"] or "")
    data = _record_dict(raw)
    if data.get(HOP_PARKED_KEY) is not True:
        return False
    if _park_released(raw):
        return False
    if str(data.get(HOP_PARK_REASON_KEY) or "") != _PARK_REASON_NO_PROGRESS_CAP:
        return False
    data[HOP_PARK_RELEASED_AT_KEY] = time.time()
    conn.execute(
        "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
        (
            json.dumps(data, sort_keys=True, separators=(",", ":")),
            pred,
        ),
    )
    admit_thread = thread_id or ""
    admit_work_key = work_key
    admit_caller = caller_agent

    def _emit(
        pid: str = pred,
        tid: str = admit_thread,
        wk: str | None = admit_work_key,
        agent: str = admit_caller,
    ) -> None:
        from services.git_integration_worker.cursor_sdk_hop_events import (
            emit_frontier_sdk_conductor_hop_park_released,
        )

        emit_frontier_sdk_conductor_hop_park_released(
            parked_dispatch_id=pid,
            thread_id=tid,
            work_key=wk,
            caller_agent=agent,
        )

    post_commit_emits.append(_emit)
    return True


def release_mission_parks(
    conn: sqlite3.Connection,
    *,
    work_key: str | None,
    thread_id: str | None,
    caller_agent: str,
    post_commit_emits: list[Callable[[], None]],
) -> tuple[ParkState, ...]:
    """Stamp every open budget park in scope; queue one emit per row after commit.

    An empty park set is still a release for *work_key*: the mission-cap
    baseline is written onto the latest terminal hop. Any non-empty budget
    park set (mission-cap, crash-cap, no-progress, admit-retry) skips that
    fallback; only a mission-cap row in the loop receives the baseline.
    """
    parks = open_parks(
        conn, work_key=work_key, thread_id=thread_id, kinds=frozenset({"budget"})
    )
    released: list[ParkState] = []
    baseline_by_work_key: dict[str, int] = {}
    for p in parks:
        row = conn.execute(
            "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (p.dispatch_id,),
        ).fetchone()
        if row is None:
            continue
        data = _record_dict(str(row["record_json"] or ""))
        data[HOP_PARK_RELEASED_AT_KEY] = time.time()
        park_work_key = str(p.work_key or "").strip()
        if p.reason == PARK_REASON_MISSION_CAP and park_work_key:
            if park_work_key not in baseline_by_work_key:
                chain = list_mission_terminal_chain(
                    work_key=park_work_key,
                    conn=conn,
                )
                baseline_by_work_key[park_work_key] = count_hop_attempts(chain)
            data[HOP_MISSION_CAP_RELEASE_BASELINE_KEY] = baseline_by_work_key[
                park_work_key
            ]
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (
                json.dumps(data, sort_keys=True, separators=(",", ":")),
                p.dispatch_id,
            ),
        )
        parked_id = p.dispatch_id
        admit_thread = thread_id or ""
        admit_work_key = work_key
        admit_caller = caller_agent

        def _emit(
            pid: str = parked_id,
            tid: str = admit_thread,
            wk: str | None = admit_work_key,
            agent: str = admit_caller,
        ) -> None:
            from services.git_integration_worker.cursor_sdk_hop_events import (
                emit_frontier_sdk_conductor_hop_park_released,
            )

            emit_frontier_sdk_conductor_hop_park_released(
                parked_dispatch_id=pid,
                thread_id=tid,
                work_key=wk,
                caller_agent=agent,
            )

        post_commit_emits.append(_emit)
        released.append(
            ParkState(
                parked_dispatch_id=p.dispatch_id,
                reason=p.reason,
                hop_seq=p.hop_seq,
                parked_at=p.parked_at,
            )
        )
    if not parks:
        _stamp_mission_cap_baseline_on_latest_terminal(conn, work_key=work_key)
    return tuple(released)


@contextlib.contextmanager
def emit_after_commit(emits: list[Callable[[], None]]):
    """Run emit callables only after the wrapped block exits without exception."""
    try:
        yield emits
    except Exception:
        raise
    else:
        for fn in emits:
            try:
                fn()
            except Exception:
                _LOG.exception("post-commit park release emit failed")


__all__ = [
    "CONDUCTOR_MISSION_PARKED_CODE",
    "ConductorMissionParked",
    "HOP_PARK_RELEASED_AT_KEY",
    "OPEN_RESTART_PARK_SQL",
    "open_restart_park_sql",
    "OpenPark",
    "PARK_KINDS",
    "ParkState",
    "emit_after_commit",
    "mission_park_state",
    "open_parks",
    "refuse_parked_conductor_mission",
    "release_mission_parks",
]
