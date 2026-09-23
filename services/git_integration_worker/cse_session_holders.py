"""CSE session holder registration — durable lane occupants in GIW SQLite."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.holder_strings import holder_id_from_chat_url
from universal_logging import get_logger

logger = get_logger(__name__)

_VALID_NEST_PARENT = frozenset({"driving", "dormant"})
_OCCUPANCY_STATES = frozenset({"driving", "dormant"})
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
    """Idempotent upsert keyed by ``holder_id`` from normalized ``chat_url``."""
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
        return get_holder(conn, hid) or {}
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
    return get_holder(conn, hid) or {}


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
            and existing_state == "driving"
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
    base = os.environ.get(
        "GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091"
    ).rstrip("/")
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


def upsert_holder_remote(
    *,
    chat_url: str,
    registration_id: str | None = None,
    execution_id: str | None = None,
    lane_thread_id: str | None = None,
) -> bool:
    """Fail-open HTTP upsert for out-of-process callers (cdp_registry hook)."""
    base = os.environ.get(
        "GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091"
    ).rstrip("/")
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
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.debug("cse holder remote upsert fail-open: %s", exc)
        return False


from services.git_integration_worker.cse_holder_boot_reconcile import (  # noqa: E402
    boot_reconcile,
)

__all__ = [
    "boot_reconcile",
    "ensure_schema",
    "get_driving_holder_for_lane",
    "get_holder",
    "occupy_holder_on_hop",
    "occupancy_projections",
    "resolve_nest_parent",
    "transition_seat_state",
    "release_holder_for_registration",
    "release_holder_remote",
    "upsert_holder",
    "upsert_holder_remote",
]
