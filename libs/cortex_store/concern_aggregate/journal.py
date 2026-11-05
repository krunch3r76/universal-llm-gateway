"""Append-only concern journal with atomic view updates."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from cortex_store.db import json_encode

from .advisory_outbox import enqueue_outbox
from .fault_injection import (
    maybe_after_cursor_update,
    maybe_after_head_update,
    maybe_after_journal_insert,
)
from .identities import (
    IntentId,
    RecordRef,
    parse_transition_identities,
)
from .views import contiguous_through_seq

FOLD_VERSION = 1
SCHEMA_VERSION = 1

_TRANSITION_KINDS = frozenset(
    {
        "position_head",
        "control_set",
        "control_clear",
        "action_intent",
        "fenced_claim",
        "receipt",
        "rollback",
    }
)


class TransitionRejectedError(ValueError):
    """Admission refused for an unauthorized or invalid transition."""


def resolve_authorized_by(conn: sqlite3.Connection, authorized_by: str) -> None:
    """Require a resolvable decision or assertion ref (a:42487)."""
    if not authorized_by or ":" not in authorized_by:
        raise TransitionRejectedError("authorized_by must be a typed entity ref")
    prefix, _rest = authorized_by.split(":", 1)
    if prefix not in {"decision", "assertion"}:
        raise TransitionRejectedError(
            f"authorized_by prefix must be decision or assertion, got {prefix!r}"
        )
    row = conn.execute(
        "SELECT 1 FROM entities WHERE id = ? LIMIT 1", (authorized_by,)
    ).fetchone()
    if row is None:
        raise TransitionRejectedError(f"authorized_by not resolvable: {authorized_by!r}")


def _ensure_seq_alloc(conn: sqlite3.Connection, concern_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO concern_seq_alloc (concern_id, next_seq) VALUES (?, 1)",
        (concern_id,),
    )


def _allocate_seq(conn: sqlite3.Connection, concern_id: str) -> int:
    _ensure_seq_alloc(conn, concern_id)
    row = conn.execute(
        "SELECT next_seq FROM concern_seq_alloc WHERE concern_id = ?",
        (concern_id,),
    ).fetchone()
    seq = int(row[0])
    conn.execute(
        "UPDATE concern_seq_alloc SET next_seq = next_seq + 1 WHERE concern_id = ?",
        (concern_id,),
    )
    return seq


def _read_head_row(
    conn: sqlite3.Connection, concern_id: str
) -> tuple[int, int, str | None]:
    row = conn.execute(
        "SELECT revision, fencing_generation, record_ref FROM concern_head "
        "WHERE concern_id = ?",
        (concern_id,),
    ).fetchone()
    if row is None:
        return 0, 0, None
    return int(row[0]), int(row[1]), row[2]


def _apply_transition_to_views(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    kind: str,
    payload: dict[str, Any],
    record_ref: RecordRef,
    intent_id: IntentId | None,
    revision: int,
    fencing_generation: int,
) -> None:
    if kind == "position_head":
        conn.execute(
            "INSERT INTO concern_head "
            "(concern_id, revision, record_ref, fencing_generation, fold_version) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(concern_id) DO UPDATE SET "
            "revision=excluded.revision, record_ref=excluded.record_ref, "
            "fencing_generation=excluded.fencing_generation, fold_version=excluded.fold_version",
            (
                concern_id,
                revision,
                str(record_ref),
                fencing_generation,
                FOLD_VERSION,
            ),
        )
        maybe_after_head_update()
    elif kind == "control_set":
        key = str(payload.get("control_key", ""))
        if not key:
            raise TransitionRejectedError("control_set requires control_key")
        conn.execute(
            "INSERT INTO concern_controls (concern_id, control_key, hold_json) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(concern_id, control_key) DO UPDATE SET hold_json=excluded.hold_json",
            (concern_id, key, json_encode(payload.get("hold", payload))),
        )
    elif kind == "control_clear":
        key = str(payload.get("control_key", ""))
        if not key:
            raise TransitionRejectedError("control_clear requires control_key")
        conn.execute(
            "DELETE FROM concern_controls WHERE concern_id = ? AND control_key = ?",
            (concern_id, key),
        )
    elif kind == "action_intent":
        if intent_id is None:
            raise TransitionRejectedError("action_intent requires intent_id")
        payload.setdefault("intent_id", str(intent_id))
        conn.execute(
            "INSERT INTO concern_intents "
            "(concern_id, intent_id, record_ref, intent_json) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(concern_id, intent_id) DO UPDATE SET "
            "record_ref=excluded.record_ref, intent_json=excluded.intent_json",
            (
                concern_id,
                str(intent_id),
                str(record_ref),
                json_encode(payload),
            ),
        )
    elif kind == "rollback":
        conn.execute(
            "INSERT INTO concern_head "
            "(concern_id, revision, record_ref, fencing_generation, fold_version) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(concern_id) DO UPDATE SET "
            "revision=excluded.revision, record_ref=excluded.record_ref, "
            "fencing_generation=excluded.fencing_generation, fold_version=excluded.fold_version",
            (
                concern_id,
                revision,
                str(record_ref),
                fencing_generation,
                FOLD_VERSION,
            ),
        )
        maybe_after_head_update()
    elif kind in {"fenced_claim", "receipt"}:
        pass
    else:
        raise TransitionRejectedError(f"unknown transition kind: {kind!r}")


def append_transition(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
    record_ref: str,
    authorized_by: str,
    intent_id: str | None = None,
    request_id: str | None = None,
    delivery_id: str | None = None,
    emit_advisory: bool = True,
) -> dict[str, Any]:
    """Accept one transition: journal row + view updates + outbox in one transaction."""
    if kind not in _TRANSITION_KINDS:
        raise TransitionRejectedError(f"invalid kind: {kind!r}")
    payload = dict(payload or {})
    rec, iid, _rid, _did = parse_transition_identities(
        record_ref=record_ref,
        intent_id=intent_id,
        request_id=request_id,
        delivery_id=delivery_id,
    )
    resolve_authorized_by(conn, authorized_by)

    prev_revision, prev_fence, _prev_ref = _read_head_row(conn, concern_id)
    revision = prev_revision + 1
    fencing_generation = prev_fence
    if kind == "rollback":
        fencing_generation = max(prev_fence, int(payload.get("fencing_generation", prev_fence)))
    elif "fencing_generation" in payload:
        fencing_generation = max(prev_fence, int(payload["fencing_generation"]))

    conn.execute("BEGIN IMMEDIATE")
    try:
        seq = _allocate_seq(conn, concern_id)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO concern_journal "
            "(concern_id, seq, record_ref, kind, payload, authorized_by, "
            "fencing_generation, revision, fold_version, schema_version, committed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                concern_id,
                seq,
                str(rec),
                kind,
                json_encode(payload),
                authorized_by,
                fencing_generation,
                revision,
                FOLD_VERSION,
                SCHEMA_VERSION,
                None,
            ),
        )
        maybe_after_journal_insert()

        _apply_transition_to_views(
            conn,
            concern_id=concern_id,
            kind=kind,
            payload=payload,
            record_ref=rec,
            intent_id=iid,
            revision=revision,
            fencing_generation=fencing_generation,
        )

        conn.execute(
            "UPDATE concern_journal SET committed_at = ? "
            "WHERE concern_id = ? AND seq = ?",
            (now, concern_id, seq),
        )

        through = contiguous_through_seq(conn, concern_id)
        cursor_token = f"cur:{through}"
        conn.execute(
            "INSERT INTO concern_cursor (concern_id, through_seq, cursor_token) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(concern_id) DO UPDATE SET "
            "through_seq=excluded.through_seq, cursor_token=excluded.cursor_token",
            (concern_id, through, cursor_token),
        )
        maybe_after_cursor_update()

        enqueue_outbox(
            conn,
            concern_id=concern_id,
            seq=seq,
            record_ref=str(rec),
            payload={
                "event_kind": "transition",
                "kind": kind,
                "seq": seq,
                "record_ref": str(rec),
            },
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    if emit_advisory:
        from .advisory_outbox import emit_pending

        emit_pending(conn, concern_id=concern_id)

    return {
        "concern_id": concern_id,
        "seq": seq,
        "record_ref": str(rec),
        "revision": revision,
        "fencing_generation": fencing_generation,
        "through_seq": through,
    }
