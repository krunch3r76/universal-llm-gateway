"""CSE session holder registration — durable lane occupants in GIW SQLite."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.holder_strings import holder_id_from_chat_url
from universal_logging import get_logger

logger = get_logger(__name__)

_VALID_NEST_PARENT = frozenset({"driving", "dormant"})
_OCCUPANCY_STATES = frozenset({"driving", "dormant"})
# Month-old test leftovers (cse_abc123, cse_THROW*, cse_ttl0) stay driving
# because boot reconcile only runs at process start and skips an empty
# registry. Six hours keeps a just-bound row (including unit-test fixtures)
# and still drops a holder the live registry no longer names.
_STALE_DRIVING_ABSENT_S = 6 * 3600
_SEAT_STATES = frozenset({"driving", "dormant", "superseded", "released"})

_DDL = """
CREATE TABLE IF NOT EXISTS cse_session_holders (
  holder_id TEXT PRIMARY KEY,
  chat_url TEXT NOT NULL,
  registration_id TEXT,
  execution_id TEXT,
  lane_thread_id TEXT,
  seat_state TEXT NOT NULL CHECK(seat_state IN ('driving','dormant','superseded','released')),
  superseded_by TEXT,
  work_key TEXT,
  score_journal_uri TEXT,
  bound_at TEXT NOT NULL,
  last_transition_at TEXT NOT NULL,
  record_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_cse_holders_lane
    ON cse_session_holders(lane_thread_id, seat_state);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create ``cse_session_holders`` table and index if absent."""
    conn.executescript(_DDL)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _emit(signal: str, payload: dict[str, Any]) -> None:
    from services.git_integration_worker.events import publish_lib_signal

    with contextlib.suppress(Exception):
        publish_lib_signal(signal, payload)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def get_holder(conn: sqlite3.Connection, holder_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM cse_session_holders WHERE holder_id=?",
        (holder_id,),
    ).fetchone()
    return _row_to_dict(row)


def get_driving_holder_for_lane(
    conn: sqlite3.Connection, lane_thread_id: str
) -> dict[str, Any] | None:
    lane = (lane_thread_id or "").strip()
    if not lane:
        return None
    row = conn.execute(
        "SELECT * FROM cse_session_holders "
        "WHERE lane_thread_id=? AND seat_state='driving' "
        "ORDER BY last_transition_at DESC LIMIT 1",
        (lane,),
    ).fetchone()
    return _row_to_dict(row)


def resolve_nest_parent(
    conn: sqlite3.Connection, holder_id: str
) -> dict[str, Any] | None:
    """Return holder row when valid CSE nest parent; else None."""
    row = get_holder(conn, holder_id)
    if row is None:
        return None
    if str(row.get("seat_state") or "") not in _VALID_NEST_PARENT:
        return None
    return row


class RemoteUpsert(str, Enum):
    """Outcome of ``upsert_holder_remote`` (one POST, no retry)."""

    OK = "ok"
    LANE_CONFLICT = "lane_conflict"
    UNAVAILABLE = "unavailable"


class HolderLaneConflictError(Exception):
    """Live holder refused a move onto a different continuity lane.

    Raised by ``upsert_holder`` when the row is driving or dormant and already
    bound to another ``lane_thread_id``. Callers map this to HTTP 409. Released
    and superseded rows are not conflicts; they may rebind.
    """

    def __init__(
        self,
        *,
        holder_id: str,
        current_lane: str,
        requested_lane: str,
    ) -> None:
        self.holder_id = holder_id
        self.current_lane = current_lane
        self.requested_lane = requested_lane
        super().__init__(
            "lane_conflict "
            f"holder={holder_id} current={current_lane} requested={requested_lane}"
        )


def upsert_holder(
    conn: sqlite3.Connection,
    *,
    chat_url: str,
    registration_id: str | None = None,
    execution_id: str | None = None,
    lane_thread_id: str | None = None,
    work_key: str | None = None,
    score_journal_uri: str | None = None,
) -> dict[str, Any]:
    """Idempotent holder bind keyed by chat URL, refusing a live cross-lane steal.

    A driving or dormant row whose ``lane_thread_id`` is already set stays on
    that lane when the request names a different one (``HolderLaneConflictError``;
    no column write). Released and superseded rows rebind and return to
    driving. Any upsert that leaves a driving row supersedes the other
    driving and dormant rows on that lane (``superseded_by`` is the new
    holder id) and emits ``cse.holder.lane_conflict`` or the helper's
    ``cse.holder.superseded``.
    """
    url = normalize_cse_url(chat_url)
    hid = holder_id_from_chat_url(url)
    if not hid:
        raise ValueError(f"invalid CSE chat_url: {chat_url!r}")
    now = _now()
    existing = get_holder(conn, hid)
    reg = (registration_id or "").strip() or None
    exec_id = (execution_id or "").strip() or None
    lane = (lane_thread_id or "").strip() or None
    if existing is not None:
        prior_state = str(existing.get("seat_state") or "")
        prior_lane = str(existing.get("lane_thread_id") or "").strip() or None
        if (
            lane
            and prior_lane
            and lane != prior_lane
            and prior_state in _OCCUPANCY_STATES
        ):
            _emit(
                "cse.holder.lane_conflict",
                {
                    "holder_id": hid,
                    "current_lane": prior_lane,
                    "requested_lane": lane,
                    "seat_state": prior_state,
                },
            )
            raise HolderLaneConflictError(
                holder_id=hid,
                current_lane=prior_lane,
                requested_lane=lane,
            )
        conn.execute(
            "UPDATE cse_session_holders SET chat_url=?, registration_id=?, "
            "execution_id=?, lane_thread_id=COALESCE(?, lane_thread_id), "
            "work_key=COALESCE(?, work_key), "
            "score_journal_uri=COALESCE(?, score_journal_uri), "
            "last_transition_at=? WHERE holder_id=?",
            (
                url,
                reg,
                exec_id,
                lane,
                work_key,
                score_journal_uri,
                now,
                hid,
            ),
        )
        if prior_state in {"superseded", "released"}:
            conn.execute(
                "UPDATE cse_session_holders SET seat_state='driving', "
                "superseded_by=NULL, last_transition_at=? WHERE holder_id=?",
                (now, hid),
            )
            _emit(
                "cse.holder.relaunched",
                {
                    "holder_id": hid,
                    "registration_id": reg,
                    "execution_id": exec_id,
                },
            )
        row = get_holder(conn, hid) or {}
        _supersede_if_driving(conn, row)
        return get_holder(conn, hid) or row
    conn.execute(
        "INSERT INTO cse_session_holders "
        "(holder_id, chat_url, registration_id, execution_id, lane_thread_id, "
        " seat_state, superseded_by, work_key, score_journal_uri, "
        " bound_at, last_transition_at, record_json) "
        "VALUES (?, ?, ?, ?, ?, 'driving', NULL, ?, ?, ?, ?, '{}')",
        (
            hid,
            url,
            reg,
            exec_id,
            lane,
            work_key,
            score_journal_uri,
            now,
            now,
        ),
    )
    _emit(
        "cse.holder.bound",
        {
            "holder_id": hid,
            "chat_url": url,
            "registration_id": reg,
            "lane_thread_id": lane,
        },
    )
    row = get_holder(conn, hid) or {}
    _supersede_if_driving(conn, row)
    return get_holder(conn, hid) or row


def _supersede_if_driving(conn: sqlite3.Connection, row: dict[str, Any]) -> list[str]:
    """Supersede other live rows when *row* is the driving holder on its lane."""
    if str(row.get("seat_state") or "") != "driving":
        return []
    lane = str(row.get("lane_thread_id") or "").strip()
    hid = str(row.get("holder_id") or "").strip()
    if not lane or not hid:
        return []
    return _supersede_lane_predecessors(
        conn,
        lane_thread_id=lane,
        occupy_holder_id=hid,
        superseded_registration_id=None,
        superseded_by=hid,
    )


def transition_seat_state(
    conn: sqlite3.Connection,
    holder_id: str,
    *,
    to_state: str,
    superseded_by: str | None = None,
    reason: str | None = None,
    registration_id: str | None = None,
    execution_id: str | None = None,
) -> dict[str, Any] | None:
    """Apply one ``seat_state`` transition with advisory event emission."""
    if to_state not in _SEAT_STATES:
        raise ValueError(f"invalid seat_state: {to_state!r}")
    row = get_holder(conn, holder_id)
    if row is None:
        return None
    from_state = str(row.get("seat_state") or "")
    if from_state == to_state:
        return row
    now = _now()
    conn.execute(
        "UPDATE cse_session_holders SET seat_state=?, superseded_by=?, "
        "registration_id=COALESCE(?, registration_id), "
        "execution_id=COALESCE(?, execution_id), last_transition_at=? "
        "WHERE holder_id=?",
        (
            to_state,
            superseded_by,
            registration_id,
            execution_id,
            now,
            holder_id,
        ),
    )
    event_map = {
        ("driving", "dormant"): "cse.holder.dormant",
        ("dormant", "driving"): "cse.holder.relaunched",
        ("driving", "superseded"): "cse.holder.superseded",
    }
    signal = event_map.get((from_state, to_state))
    if signal:
        payload: dict[str, Any] = {"holder_id": holder_id}
        if superseded_by:
            payload["superseded_by"] = superseded_by
        if registration_id:
            payload["registration_id"] = registration_id
        if execution_id:
            payload["execution_id"] = execution_id
        _emit(signal, payload)
    elif to_state == "released":
        _emit(
            "cse.holder.released",
            {"holder_id": holder_id, "reason": reason or "lane_release"},
        )
    return get_holder(conn, holder_id)


def _supersede_lane_predecessors(
    conn: sqlite3.Connection,
    *,
    lane_thread_id: str,
    occupy_holder_id: str,
    superseded_registration_id: str | None,
    superseded_by: str | None,
) -> list[str]:
    """Mark predecessor holder rows on *lane_thread_id* as superseded."""
    lane = (lane_thread_id or "").strip()
    if not lane:
        return []
    superseded_ids: list[str] = []
    rows = conn.execute(
        "SELECT holder_id, registration_id, seat_state FROM cse_session_holders "
        "WHERE lane_thread_id=? AND seat_state IN ('driving', 'dormant')",
        (lane,),
    ).fetchall()
    target_reg = (superseded_by or "").strip() or None
    prior_reg = (superseded_registration_id or "").strip() or None
    for row in rows:
        hid = str(row["holder_id"])
        if hid == occupy_holder_id:
            continue
        # Same-lane driving/dormant rows are predecessors even when they
        # have no registration_id (AC1 missed-mint). prior_reg only
        # widens the search off-lane below.
        reg = str(row["registration_id"] or "").strip()
        transition_seat_state(
            conn,
            hid,
            to_state="superseded",
            superseded_by=target_reg,
            registration_id=reg or None,
        )
        superseded_ids.append(hid)
    if prior_reg:
        for row in conn.execute(
            "SELECT holder_id, registration_id FROM cse_session_holders "
            "WHERE registration_id=? AND seat_state IN ('driving', 'dormant')",
            (prior_reg,),
        ).fetchall():
            hid = str(row["holder_id"])
            if hid == occupy_holder_id or hid in superseded_ids:
                continue
            transition_seat_state(
                conn,
                hid,
                to_state="superseded",
                superseded_by=target_reg,
                registration_id=prior_reg,
            )
            superseded_ids.append(hid)
    return superseded_ids


def _union_ids(left: list[str], right: list[str]) -> list[str]:
    seen: list[str] = []
    for hid in [*left, *right]:
        if hid and hid not in seen:
            seen.append(hid)
    return seen


def _holders_marked_superseded_by(
    conn: sqlite3.Connection,
    lane_thread_id: str,
    markers: list[str],
) -> list[str]:
    """Holder ids on *lane_thread_id* already stamped superseded by *markers*."""
    lane = (lane_thread_id or "").strip()
    marks = [m.strip() for m in markers if m and m.strip()]
    if not lane or not marks:
        return []
    placeholders = ",".join("?" * len(marks))
    rows = conn.execute(
        "SELECT holder_id FROM cse_session_holders "
        f"WHERE lane_thread_id=? AND seat_state='superseded' "
        f"AND superseded_by IN ({placeholders})",
        (lane, *marks),
    ).fetchall()
    return [str(row["holder_id"]) for row in rows]


def seat_successor_on_lane(
    conn: sqlite3.Connection,
    *,
    chat_url: str,
    registration_id: str,
    lane_thread_id: str,
    execution_id: str | None = None,
) -> dict[str, Any]:
    """Drive *registration_id* on *lane_thread_id* and supersede other live rows.

    The holder key is the successor window URL. Same-lane driving and dormant
    rows, including a missed-mint with no registration, become ``superseded``.
    """
    reg = (registration_id or "").strip()
    lane = (lane_thread_id or "").strip()
    if not reg or not lane:
        return {
            "ok": False,
            "reason": "missing_successor_or_lane",
            "holder": None,
            "superseded_holder_ids": [],
        }
    url = normalize_cse_url(chat_url)
    hid = holder_id_from_chat_url(url)
    existing = get_holder(conn, hid) if hid else None
    if existing is not None:
        existing_lane = str(existing.get("lane_thread_id") or "").strip()
        existing_state = str(existing.get("seat_state") or "")
        if (
            existing_lane
            and existing_lane != lane
            and existing_state in _OCCUPANCY_STATES
        ):
            return {
                "ok": False,
                "reason": "occupy_target_held_by_live_peer",
                "holder": existing,
                "held_by_lane": existing_lane,
                "current_lane": existing_lane,
                "requested_lane": lane,
                "superseded_holder_ids": [],
            }
    row = upsert_holder(
        conn,
        chat_url=chat_url,
        registration_id=reg,
        execution_id=execution_id,
        lane_thread_id=lane,
    )
    hid = str(row.get("holder_id") or "")
    # upsert relaunches only superseded/released. A dormant window on this
    # URL would otherwise stay dormant while its peers are superseded.
    if hid and str(row.get("seat_state") or "") != "driving":
        repaired = transition_seat_state(
            conn,
            hid,
            to_state="driving",
            superseded_by=None,
            registration_id=reg,
            execution_id=execution_id,
        )
        if repaired is not None:
            row = repaired
    superseded = _supersede_lane_predecessors(
        conn,
        lane_thread_id=lane,
        occupy_holder_id=hid,
        superseded_registration_id=None,
        superseded_by=reg,
    )
    superseded = _union_ids(
        superseded,
        _holders_marked_superseded_by(conn, lane, [hid, reg]),
    )
    return {
        "ok": True,
        "holder": get_holder(conn, hid) or row,
        "superseded_holder_ids": [i for i in superseded if i != hid],
    }


def occupy_holder_on_hop(
    conn: sqlite3.Connection,
    *,
    occupy_target: str,
    lane_thread_id: str,
    superseded_registration_id: str | None = None,
    new_registration_id: str | None = None,
    new_execution_id: str | None = None,
) -> dict[str, Any]:
    """Occupy an existing holder row on continuity hop; supersede predecessor.

    AC3 bind (agent-bus:11667):
    - absent row → mint fresh + ``occupy_missed`` (not silent peer eviction)
    - different live peer on another lane → refuse (not upsert-and-evict)
    """
    url = normalize_cse_url(occupy_target)
    hid = holder_id_from_chat_url(url)
    if not hid:
        return {
            "ok": False,
            "path": "invalid_occupy_target",
            "reason": "invalid_occupy_target",
        }
    lane = (lane_thread_id or "").strip()
    existing = get_holder(conn, hid)
    if existing is not None:
        existing_lane = str(existing.get("lane_thread_id") or "").strip()
        existing_state = str(existing.get("seat_state") or "")
        if (
            existing_lane
            and existing_lane != lane
            and existing_state in _OCCUPANCY_STATES
        ):
            _emit(
                "cse.holder.occupy_refused",
                {
                    "holder_id": hid,
                    "occupy_target": url,
                    "lane_thread_id": lane,
                    "held_by_lane": existing_lane,
                    "reason": "occupy_target_held_by_live_peer",
                },
            )
            return {
                "ok": False,
                "path": "refused_live_peer",
                "reason": "occupy_target_held_by_live_peer",
                "holder_id": hid,
                "held_by_lane": existing_lane,
            }
    path = "occupy_upsert"
    if existing is None:
        path = "occupy_missed_mint"
    row = upsert_holder(
        conn,
        chat_url=url,
        registration_id=new_registration_id,
        execution_id=new_execution_id,
        lane_thread_id=lane or None,
    )
    if path == "occupy_missed_mint":
        _emit(
            "cse.holder.occupy_missed",
            {
                "holder_id": hid,
                "occupy_target": url,
                "lane_thread_id": lane,
            },
        )
    superseded_holders = _supersede_lane_predecessors(
        conn,
        lane_thread_id=lane,
        occupy_holder_id=hid,
        superseded_registration_id=superseded_registration_id,
        superseded_by=new_registration_id or hid,
    )
    superseded_holders = _union_ids(
        superseded_holders,
        _holders_marked_superseded_by(
            conn,
            lane,
            [hid, new_registration_id or ""],
        ),
    )
    superseded_holders = [i for i in superseded_holders if i != hid]
    _emit(
        "cse.holder.occupy",
        {
            "holder_id": hid,
            "path": path,
            "lane_thread_id": lane,
            "registration_id": new_registration_id,
            "execution_id": new_execution_id,
            "superseded_registration_id": superseded_registration_id,
            "superseded_holder_ids": superseded_holders,
        },
    )
    return {
        "ok": True,
        "path": path,
        "holder_id": hid,
        "registration_id": row.get("registration_id"),
        "chat_url": row.get("chat_url"),
        "superseded_holder_ids": superseded_holders,
    }


def _bound_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def reap_stale_driving_holders(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    min_age_s: float = _STALE_DRIVING_ABSENT_S,
    registry_urls: set[str] | None = None,
) -> int:
    """Release driving rows older than ``min_age_s`` that the registry does not name.

    A failed registry read and an empty snapshot release nothing. Young rows
    stay so a just-bound holder is not raced off the table. Returns the
    number of rows transitioned to ``released``.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(seconds=min_age_s)
    rows = conn.execute(
        "SELECT holder_id, chat_url, bound_at FROM cse_session_holders "
        "WHERE seat_state='driving'"
    ).fetchall()
    stale: list[tuple[str, str]] = []
    for row in rows:
        bound = _bound_at(row["bound_at"])
        if bound is None or bound > cutoff:
            continue
        stale.append((str(row["holder_id"]), str(row["chat_url"] or "")))
    if not stale:
        return 0
    if registry_urls is None:
        from services.git_integration_worker.cse_holder_boot_reconcile import (
            live_registry_urls,
        )

        registry_urls = live_registry_urls()
    if not registry_urls:
        return 0
    released = 0
    for holder_id, chat_url in stale:
        if normalize_cse_url(chat_url) in registry_urls:
            continue
        transition_seat_state(
            conn,
            holder_id,
            to_state="released",
            reason="stale_driving_absent_registry",
        )
        released += 1
    return released


def reap_stale_driving_holders_fail_open(
    conn: sqlite3.Connection, **kwargs: Any
) -> int:
    """Reap stale holders without failing the caller.

    ``GET /active-work`` is the drain probe. A locked ledger or a raise inside
    ``transition_seat_state`` must not turn that probe into a 500 or a 30s stall
    (review of friction:39833, F3).
    """
    try:
        return reap_stale_driving_holders(conn, **kwargs)
    except Exception:
        logger.exception("cse holder reap failed; active-work continues")
        with contextlib.suppress(sqlite3.Error):
            conn.rollback()
        return 0


def occupancy_projections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Active CSE occupancy rows for ``/active-work`` (never drain-blocking)."""
    rows = conn.execute(
        "SELECT holder_id, chat_url, registration_id, execution_id, "
        "lane_thread_id, seat_state, work_key, score_journal_uri, bound_at "
        "FROM cse_session_holders WHERE seat_state IN ('driving', 'dormant') "
        "ORDER BY bound_at ASC"
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        hid = str(row["holder_id"])
        out.append(
            {
                "kind": "cowork_cse",
                "busy_class": "occupancy",
                "op_id": hid,
                "holder_id": hid,
                "chat_url": row["chat_url"],
                "registration_id": row["registration_id"],
                "execution_id": row["execution_id"],
                "lane_thread_id": row["lane_thread_id"],
                "seat_state": row["seat_state"],
                "work_key": row["work_key"],
                "score_journal_uri": row["score_journal_uri"],
                "bound_at": row["bound_at"],
                "route": "cse_session_holders",
                "state": str(row["seat_state"]),
            }
        )
    return out


def release_holder_for_registration(
    conn: sqlite3.Connection,
    *,
    chat_url: str,
    registration_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any] | None:
    """Transition holder row to ``released`` for a detached registration."""
    url = normalize_cse_url(chat_url)
    hid = holder_id_from_chat_url(url)
    if not hid:
        return None
    row = get_holder(conn, hid)
    if row is None:
        return None
    reg = (registration_id or "").strip() or None
    if reg and str(row.get("registration_id") or "").strip() not in {"", reg}:
        return row
    return transition_seat_state(
        conn,
        hid,
        to_state="released",
        reason=reason or "detach",
        registration_id=reg,
    )


def release_holder_remote(
    *,
    chat_url: str,
    registration_id: str | None = None,
    reason: str | None = None,
) -> bool:
    """Fail-open HTTP release for out-of-process ``detach``."""
    base = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091").rstrip(
        "/"
    )
    body = json.dumps(
        {
            "chat_url": chat_url,
            "registration_id": registration_id,
            "reason": reason,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/api/v1/git/admin/cse-holder/release",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("cse holder remote release fail-open: %s", exc)
        return False


def _lane_conflict_from_http_error(exc: urllib.error.HTTPError) -> dict[str, Any] | None:
    try:
        raw = exc.read()
        payload = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if isinstance(detail, dict) and detail.get("reason") == "lane_conflict":
        return detail
    if payload.get("reason") == "lane_conflict":
        return payload
    return None


def upsert_holder_remote(
    *,
    chat_url: str,
    registration_id: str | None = None,
    execution_id: str | None = None,
    lane_thread_id: str | None = None,
) -> RemoteUpsert:
    """Best-effort HTTP upsert for out-of-process callers (cdp_registry hook).

    One POST to GIW ``/cse-holder/upsert`` with no retry. Transport failure and
    non-conflict HTTP errors return ``RemoteUpsert.UNAVAILABLE`` (fail-open).
    HTTP 409 with ``lane_conflict`` returns ``RemoteUpsert.LANE_CONFLICT`` so
    callers do not treat a live cross-lane refusal as success or as GIW-down.
    """
    base = os.environ.get("GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091").rstrip(
        "/"
    )
    body = json.dumps(
        {
            "chat_url": chat_url,
            "registration_id": registration_id,
            "execution_id": execution_id,
            "lane_thread_id": lane_thread_id,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/api/v1/git/admin/cse-holder/upsert",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if 200 <= resp.status < 300:
                return RemoteUpsert.OK
            logger.debug(
                "cse holder remote upsert fail-open: unexpected status %s",
                resp.status,
            )
            return RemoteUpsert.UNAVAILABLE
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            conflict = _lane_conflict_from_http_error(exc)
            if conflict is not None:
                logger.warning(
                    "cse holder remote upsert lane_conflict chat_url=%s "
                    "registration_id=%s current_lane=%s requested_lane=%s",
                    chat_url,
                    registration_id,
                    conflict.get("current_lane"),
                    conflict.get("requested_lane"),
                )
                return RemoteUpsert.LANE_CONFLICT
        logger.debug("cse holder remote upsert fail-open: %s", exc)
        return RemoteUpsert.UNAVAILABLE
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("cse holder remote upsert fail-open: %s", exc)
        return RemoteUpsert.UNAVAILABLE


from services.git_integration_worker.cse_holder_boot_reconcile import (  # noqa: E402
    boot_reconcile,
)


def _ledger_conn():
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance()._connect()


def resolve_maestro_holder_id_for_lane(lane_thread_id: str | None) -> str | None:
    """Return driving/dormant holder id for a continuity lane, if any."""
    lane = (lane_thread_id or "").strip()
    if not lane:
        return None
    with _ledger_conn() as conn:
        ensure_schema(conn)
        row = get_driving_holder_for_lane(conn, lane)
        if row is None:
            row = conn.execute(
                "SELECT * FROM cse_session_holders "
                "WHERE lane_thread_id=? AND seat_state IN ('driving', 'dormant') "
                "ORDER BY last_transition_at DESC LIMIT 1",
                (lane,),
            ).fetchone()
            row = _row_to_dict(row)
    if row is None:
        return None
    return str(row.get("holder_id") or "") or None


def resolve_holder_by_registration(
    conn: sqlite3.Connection, registration_id: str
) -> dict[str, Any] | None:
    reg = (registration_id or "").strip()
    if not reg:
        return None
    row = conn.execute(
        "SELECT * FROM cse_session_holders WHERE registration_id=? "
        "ORDER BY last_transition_at DESC LIMIT 1",
        (reg,),
    ).fetchone()
    return _row_to_dict(row)


def lookup_holder_for_owed_wake(holder_id: str) -> dict[str, Any] | None:
    hid = (holder_id or "").strip()
    if not hid:
        return None
    with _ledger_conn() as conn:
        ensure_schema(conn)
        return get_holder(conn, hid)


def resolve_superseded_holder_live(registration_id: str) -> dict[str, Any] | None:
    with _ledger_conn() as conn:
        ensure_schema(conn)
        row = resolve_holder_by_registration(conn, registration_id)
        if row is None:
            return None
        state = str(row.get("seat_state") or "")
        if state in {"driving", "dormant"}:
            return row
    return None


def lane_thread_uses_bridge_wake(lane_thread_id: str) -> bool:
    try:
        from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (  # noqa: E501
            _summoning_thread_uses_xvfb_push_wake,
        )

        return _summoning_thread_uses_xvfb_push_wake(str(lane_thread_id))
    except Exception:
        return False


def agent_bus_request_headers() -> dict[str, str]:
    """Bearer + JSON headers for agent_bus UDS/HTTP clients (matches _post_bus)."""
    import os

    token = os.environ.get("AGENT_BUS_TOKEN", "").strip()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def close_owed_wake_on_lane_activity(
    *,
    lane_thread_id: str | None,
    holder_id: str | None = None,
    seat_agent: str | None = None,
) -> None:
    """Close open owed_wake rows when the addressed holder seat marks read or posts."""
    from scripts.model_manager.ui.controller.owed_wake_ledger import (
        producer1_to_agent_qualifies,
    )

    agent = (seat_agent or "").strip().lower()
    if not producer1_to_agent_qualifies(agent):
        return
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    store = RestartIntentStore.instance()
    hid = (holder_id or "").strip()
    if not hid and lane_thread_id:
        hid = resolve_maestro_holder_id_for_lane(lane_thread_id) or ""
    if hid:
        store.close_owed_wake_for_holder(hid)


def on_house_milestone_to_maestro(
    *,
    subject: str,
    to_agent: str,
    from_agent: str,
    thread_id: str,
    turn_number: int,
    lane_thread_id: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Producer 1 entry — allowlisted house milestone posts to maestro."""
    from scripts.model_manager.ui.controller.owed_wake_ledger import (
        maybe_producer1_milestone,
        producer1_cse_dispatch_enabled,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    lane = (lane_thread_id or thread_id or "").strip()
    holder_id = resolve_maestro_holder_id_for_lane(lane)
    if not holder_id:
        return {"recorded": False, "reason": "no_holder_for_lane"}
    store = RestartIntentStore.instance()
    result = maybe_producer1_milestone(
        store,
        subject=subject,
        to_agent=to_agent,
        from_agent=from_agent,
        thread_id=thread_id,
        turn_number=turn_number,
        holder_id=holder_id,
        lane_thread_id=lane,
        user_agent=user_agent,
        deps=None,
    )
    if (
        result.get("recorded")
        and result.get("created")
        and producer1_cse_dispatch_enabled()
    ):
        result["needs_deferred_dispatch"] = True
    return result


def _import_cse_session_warm_module():
    import sys
    from pathlib import Path

    mcp_root = Path(__file__).resolve().parents[1] / "mcp-server"
    mcp_str = str(mcp_root)
    if mcp_str not in sys.path:
        sys.path.insert(0, mcp_str)
    from tools import cse_session_warm

    return cse_session_warm


def build_owed_wake_deps():
    from scripts.model_manager.ui.controller.owed_wake_ledger import OwedWakeDeps

    cse_session_warm = _import_cse_session_warm_module()

    def _post_bus(*, thread_id: str, subject: str, body: str) -> bool:
        try:
            from transport_utils import DEFAULT_AGENT_BUS_URL, make_sync_client

            headers = agent_bus_request_headers()
            payload = {
                "thread": thread_id,
                "from": "manage",
                "to": "cursor",
                "subject": subject,
                "body": body,
                "status": "open",
            }
            with make_sync_client(DEFAULT_AGENT_BUS_URL, timeout=5.0) as client:
                resp = client.post("/turns", json=payload, headers=headers)
                return 200 <= resp.status_code < 300
        except Exception:
            return False

    def _endpoint_wake(
        *,
        holder_id: str,
        lane_thread_id: str | None,
        wake_kind: str,
        context: dict[str, Any],
    ) -> bool:
        if wake_kind == "bridge_wake":
            return cse_session_warm.dispatch_bridge_wake(
                lane_thread_id=lane_thread_id,
                context=context,
            )
        holder = context.get("holder") or lookup_holder_for_owed_wake(holder_id)
        if not isinstance(holder, dict):
            return False
        return cse_session_warm.dispatch_cse_holder_followup(holder=holder)

    return OwedWakeDeps(
        post_bus_turn=_post_bus,
        endpoint_wake=_endpoint_wake,
        holder_lookup=lookup_holder_for_owed_wake,
        resolve_superseded_holder=resolve_superseded_holder_live,
        lane_uses_bridge_wake=lane_thread_uses_bridge_wake,
    )


def dispatch_owed_wake_producer1_row(row_id: str) -> None:
    """Run CSE follow-up for one stored producer-1 row (off the HTTP request path)."""
    from scripts.model_manager.ui.controller.owed_wake_ledger import (
        attempt_dispatch,
        producer1_cse_dispatch_enabled,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import (
        RestartIntentStore,
    )

    if not producer1_cse_dispatch_enabled():
        return
    store = RestartIntentStore.instance()
    row = store.get_owed_wake(row_id)
    if row is None:
        return
    try:
        deps = build_owed_wake_deps()
    except Exception:
        logger.warning(
            "owed_wake producer1 dispatch skipped row_id=%s (deps unavailable)",
            row_id,
            exc_info=True,
        )
        return
    attempt_dispatch(store, row, deps=deps)


__all__ = [
    "boot_reconcile",
    "build_owed_wake_deps",
    "close_owed_wake_on_lane_activity",
    "dispatch_owed_wake_producer1_row",
    "ensure_schema",
    "HolderLaneConflictError",
    "get_driving_holder_for_lane",
    "get_holder",
    "lookup_holder_for_owed_wake",
    "occupy_holder_on_hop",
    "on_house_milestone_to_maestro",
    "occupancy_projections",
    "reap_stale_driving_holders",
    "reap_stale_driving_holders_fail_open",
    "resolve_maestro_holder_id_for_lane",
    "resolve_nest_parent",
    "resolve_superseded_holder_live",
    "seat_successor_on_lane",
    "transition_seat_state",
    "release_holder_for_registration",
    "release_holder_remote",
    "RemoteUpsert",
    "upsert_holder",
    "upsert_holder_remote",
]
