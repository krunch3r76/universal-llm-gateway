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


def boot_reconcile(conn: sqlite3.Connection) -> dict[str, Any]:
    """Reconcile holder seat fields from registry; GIW wins lane fields."""
    from claude_bundles.cdp_registry.session_address import (
        chat_url_for_registration,
        list_active,
    )

    active_urls: set[str] = set()
    registry_by_url: dict[str, dict[str, Any]] = {}
    for reg in list_active():
        url = (chat_url_for_registration(reg.registration_id) or "").strip()
        if not url:
            continue
        norm = normalize_cse_url(url)
        active_urls.add(norm)
        registry_by_url[norm] = {
            "registration_id": reg.registration_id,
            "execution_id": getattr(reg, "execution_id", None),
        }
    kept = released = updated = 0
    rows = conn.execute("SELECT holder_id, chat_url, seat_state FROM cse_session_holders").fetchall()
    for row in rows:
        url = normalize_cse_url(str(row["chat_url"] or ""))
        state = str(row["seat_state"] or "")
        if url in active_urls and state in _OCCUPANCY_STATES:
            reg = registry_by_url.get(url, {})
            conn.execute(
                "UPDATE cse_session_holders SET registration_id=?, "
                "execution_id=?, last_transition_at=? WHERE holder_id=?",
                (
                    reg.get("registration_id"),
                    reg.get("execution_id"),
                    _now(),
                    row["holder_id"],
                ),
            )
            updated += 1
            kept += 1
        elif state in _OCCUPANCY_STATES and url not in active_urls:
            transition_seat_state(
                conn,
                str(row["holder_id"]),
                to_state="released",
                reason="boot_reconcile_absent_registry",
            )
            released += 1
        else:
            kept += 1
    summary = {
        "kept": kept,
        "released": released,
        "registry_updated": updated,
    }
    _emit("cse.holder.reconcile", summary)
    return summary


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


__all__ = [
    "boot_reconcile",
    "ensure_schema",
    "get_driving_holder_for_lane",
    "get_holder",
    "occupancy_projections",
    "resolve_nest_parent",
    "transition_seat_state",
    "upsert_holder",
    "upsert_holder_remote",
]
