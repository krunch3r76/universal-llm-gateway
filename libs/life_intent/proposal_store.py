"""Restart-durable SQLite proposal store for life intent."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from . import proposal_store_db as db

PROPOSAL_TTL_SECONDS = 900
PROPOSAL_KIND_LIFE_INTENT = "life-intent"
_STATUS_OPEN = "open"
_STATUS_APPLYING = "applying"
_STATUS_FAILED = "failed"
_STATUS_INDETERMINATE = "indeterminate"
_STATUS_COMPLETED = "completed"

_RESUMABLE = frozenset({_STATUS_OPEN, _STATUS_FAILED, _STATUS_INDETERMINATE})
_PURGEABLE = frozenset(
    {_STATUS_OPEN, _STATUS_FAILED, _STATUS_INDETERMINATE, _STATUS_COMPLETED}
)

process_epoch = db.process_epoch
reset_connection_for_tests = db.reset_connection_for_tests


@dataclass(frozen=True)
class StoredProposal:
    proposal_id: str
    normalized_intent: dict[str, Any]
    work_order: str
    verb: str
    lane: str
    status: str
    created_at: datetime
    expires_at: datetime
    kind: str = PROPOSAL_KIND_LIFE_INTENT
    packet_path: str | None = None
    entity_id: str | None = None
    dispatch_ref: str | None = None
    dispatch_handle: dict[str, Any] | None = None
    reply_thread: str | None = None
    attempts: int = 0
    last_error: str | None = None
    apply_owner: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_proposal(row: Any) -> StoredProposal:
    version = int(row["schema_version"])
    if version != db.schema_version():
        raise RuntimeError(
            f"incompatible life_intent proposal schema_version={version}; "
            f"expected {db.schema_version()}"
        )
    return StoredProposal(
        proposal_id=row["proposal_id"],
        normalized_intent=json.loads(row["normalized_intent"]),
        work_order=row["work_order"],
        verb=row["verb"],
        lane=row["lane"],
        status=row["status"],
        created_at=db.parse_iso(row["created_at"]),
        expires_at=db.parse_iso(row["expires_at"]),
        kind=row["kind"],
        packet_path=row["packet_path"],
        entity_id=row["entity_id"],
        dispatch_ref=row["dispatch_ref"],
        dispatch_handle=db.decode_handle(row["dispatch_handle"]),
        reply_thread=row["reply_thread"],
        attempts=int(row["attempts"]),
        last_error=row["last_error"],
        apply_owner=row["apply_owner"],
    )


def _purge_expired(conn: Any) -> None:
    now = db.iso(_now())
    placeholders = ",".join("?" for _ in _PURGEABLE)
    conn.execute(
        f"DELETE FROM life_intent_proposals "
        f"WHERE expires_at <= ? AND status IN ({placeholders})",
        (now, *_PURGEABLE),
    )
    conn.commit()


def _fetch(conn: Any, proposal_id: str) -> StoredProposal | None:
    row = conn.execute(
        "SELECT * FROM life_intent_proposals WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_proposal(row)


def create_proposal(
    *,
    normalized_intent: dict[str, Any],
    work_order: str,
    verb: str,
    lane: str,
) -> str:
    """Mint a short-lived proposal; returns server-generated id."""
    conn = db.connect()
    with db.lock():
        _purge_expired(conn)
        proposal_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """
            INSERT INTO life_intent_proposals (
                proposal_id, schema_version, normalized_intent, work_order, verb,
                lane, status, created_at, expires_at, kind, attempts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                proposal_id,
                db.schema_version(),
                json.dumps(normalized_intent, sort_keys=True),
                work_order,
                verb,
                lane,
                _STATUS_OPEN,
                db.iso(now),
                db.iso(now + timedelta(seconds=PROPOSAL_TTL_SECONDS)),
                PROPOSAL_KIND_LIFE_INTENT,
            ),
        )
        conn.commit()
        return proposal_id


def get_proposal(proposal_id: str) -> StoredProposal | None:
    conn = db.connect()
    with db.lock():
        _purge_expired(conn)
        return _fetch(conn, proposal_id)


def is_expired(row: StoredProposal) -> bool:
    return row.expires_at <= _now()


def commit_reject_code(row: StoredProposal | None) -> str | None:
    if row is None:
        return "foreign_proposal_kind"
    if row.kind != PROPOSAL_KIND_LIFE_INTENT:
        return "foreign_proposal_kind"
    if row.status == _STATUS_COMPLETED:
        return "proposal_already_committed"
    if row.status == _STATUS_APPLYING:
        if row.apply_owner == db.process_epoch():
            return "proposal_already_committed"
        return None
    if is_expired(row):
        return "proposal_expired"
    if row.status not in _RESUMABLE:
        return "proposal_not_committable"
    return None


def begin_apply(proposal_id: str) -> tuple[StoredProposal | None, str | None]:
    """Atomically transition open|failed|indeterminate|stale-applying → applying."""
    conn = db.connect()
    with db.lock():
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = _fetch(conn, proposal_id)
            code = commit_reject_code(row)
            if code:
                conn.commit()
                return None, code
            assert row is not None
            attempts = row.attempts + 1
            conn.execute(
                """
                UPDATE life_intent_proposals
                SET status = ?, attempts = ?, apply_owner = ?, last_error = NULL
                WHERE proposal_id = ?
                """,
                (_STATUS_APPLYING, attempts, db.process_epoch(), proposal_id),
            )
            conn.commit()
            return _fetch(conn, proposal_id), None
        except Exception:
            conn.rollback()
            raise


def _update_fields(proposal_id: str, **changes: Any) -> StoredProposal | None:
    if not changes:
        return get_proposal(proposal_id)
    conn = db.connect()
    with db.lock():
        cols: list[str] = []
        vals: list[Any] = []
        for key, value in changes.items():
            if key == "dispatch_handle":
                cols.append("dispatch_handle = ?")
                vals.append(db.encode_handle(value))
            elif key == "expires_at" and isinstance(value, datetime):
                cols.append("expires_at = ?")
                vals.append(db.iso(value))
            elif key in {
                "packet_path",
                "entity_id",
                "dispatch_ref",
                "reply_thread",
                "last_error",
                "status",
                "apply_owner",
            }:
                cols.append(f"{key} = ?")
                vals.append(value)
            else:
                raise KeyError(key)
        vals.append(proposal_id)
        conn.execute(
            f"UPDATE life_intent_proposals SET {', '.join(cols)} WHERE proposal_id = ?",
            vals,
        )
        conn.commit()
        return _fetch(conn, proposal_id)


def record_packet(proposal_id: str, packet_path: str) -> StoredProposal | None:
    return _update_fields(proposal_id, packet_path=packet_path)


def record_entity(proposal_id: str, entity_id: str) -> StoredProposal | None:
    return _update_fields(proposal_id, entity_id=entity_id)


def record_dispatch_handle(
    proposal_id: str,
    handle: dict[str, Any],
    *,
    reply_thread: str | None = None,
) -> StoredProposal | None:
    """Persist prepared handle before worker POST."""
    changes: dict[str, Any] = {"dispatch_handle": dict(handle)}
    if reply_thread is not None:
        changes["reply_thread"] = reply_thread
    return _update_fields(proposal_id, **changes)


def record_dispatch(
    proposal_id: str, dispatch_ref: str, reply_thread: str
) -> StoredProposal | None:
    return _update_fields(
        proposal_id, dispatch_ref=dispatch_ref, reply_thread=reply_thread
    )


def mark_completed(proposal_id: str) -> StoredProposal | None:
    return _update_fields(
        proposal_id, status=_STATUS_COMPLETED, last_error=None, apply_owner=None
    )


def mark_failed(proposal_id: str, error: str) -> StoredProposal | None:
    return _update_fields(
        proposal_id, status=_STATUS_FAILED, last_error=error, apply_owner=None
    )


def mark_indeterminate(proposal_id: str, error: str) -> StoredProposal | None:
    return _update_fields(
        proposal_id, status=_STATUS_INDETERMINATE, last_error=error, apply_owner=None
    )


def force_expires_at(proposal_id: str, expires_at: datetime) -> StoredProposal | None:
    """Test helper — set expires_at without going through public mutators."""
    return _update_fields(proposal_id, expires_at=expires_at)


def seed_proposal(
    *,
    proposal_id: str,
    normalized_intent: dict[str, Any],
    work_order: str = "test",
    verb: str = "investigate",
    lane: str = "recon",
    status: str = _STATUS_OPEN,
    kind: str = PROPOSAL_KIND_LIFE_INTENT,
    packet_path: str | None = None,
    entity_id: str | None = None,
    dispatch_ref: str | None = None,
    dispatch_handle: dict[str, Any] | None = None,
    reply_thread: str | None = None,
    attempts: int = 0,
    last_error: str | None = None,
    apply_owner: str | None = None,
) -> None:
    """Test helper — insert a proposal row with explicit kind/status."""
    conn = db.connect()
    now = _now()
    with db.lock():
        conn.execute(
            """
            INSERT OR REPLACE INTO life_intent_proposals (
                proposal_id, schema_version, normalized_intent, work_order, verb,
                lane, status, created_at, expires_at, kind, packet_path, entity_id,
                dispatch_ref, dispatch_handle, reply_thread, attempts, last_error,
                apply_owner
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                db.schema_version(),
                json.dumps(normalized_intent, sort_keys=True),
                work_order,
                verb,
                lane,
                status,
                db.iso(now),
                db.iso(now + timedelta(seconds=PROPOSAL_TTL_SECONDS)),
                kind,
                packet_path,
                entity_id,
                dispatch_ref,
                db.encode_handle(dispatch_handle),
                reply_thread,
                attempts,
                last_error,
                apply_owner,
            ),
        )
        conn.commit()


def clear_store() -> None:
    """Test helper — delete all rows in the current DB file."""
    conn = db.connect()
    with db.lock():
        conn.execute("DELETE FROM life_intent_proposals")
        conn.commit()
