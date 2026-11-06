"""Compare-and-swap admission for ``position_head`` transitions (Phase 2)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from cortex_store.db import json_encode

from .advisory_outbox import enqueue_outbox
from .canonical_json import CanonicalJsonError, material_fingerprint
from .fault_injection import (
    maybe_after_cursor_update,
    maybe_after_journal_insert,
    maybe_after_write_intent_insert,
)
from .identities import RecordRef, as_record_ref, as_write_intent_id
from .journal import (
    FOLD_VERSION,
    SCHEMA_VERSION,
    TransitionRejectedError,
    _allocate_seq,
    _apply_transition_to_views,
    _read_head_row,
    resolve_authorized_by,
)
from .views import contiguous_through_seq


class ConcernCasError(Exception):
    """Structured CAS failure mapped to HTTP/MCP by callers."""

    def __init__(self, *, error: str, http_status: int, payload: dict[str, Any]) -> None:
        super().__init__(error)
        self.error = error
        self.http_status = http_status
        self.payload = payload


def _head_snapshot(
    conn: sqlite3.Connection, concern_id: str
) -> dict[str, Any]:
    revision, fencing_generation, record_ref = _read_head_row(conn, concern_id)
    return {
        "revision": revision,
        "record_ref": record_ref,
        "fencing_generation": fencing_generation,
    }


def _validate_expected_revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConcernCasError(
            error="invalid_expected_revision",
            http_status=422,
            payload={"error": "expected_revision must be a non-negative int"},
        )
    if value < 0:
        raise ConcernCasError(
            error="invalid_expected_revision",
            http_status=422,
            payload={"error": "expected_revision must be a non-negative int"},
        )
    return value


def apply_position_head(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    expected_revision: object,
    write_intent_id: str,
    material: dict[str, Any],
    record_ref: str,
    authorized_by: str,
    payload: dict[str, Any] | None = None,
    emit_advisory: bool = True,
) -> dict[str, Any]:
    """Atomic CAS write for ``position_head`` (write intent first, then revision)."""
    exp = _validate_expected_revision(expected_revision)
    wi = as_write_intent_id(write_intent_id)
    rec = as_record_ref(record_ref)
    body = dict(payload or {})
    try:
        fp = material_fingerprint(material)
    except CanonicalJsonError as exc:
        raise TransitionRejectedError(str(exc)) from exc

    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT material_fingerprint, result_record_ref, result_revision, result_seq "
            "FROM concern_write_intents WHERE concern_id = ? AND write_intent_id = ?",
            (concern_id, str(wi)),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != fp:
                conn.rollback()
                raise ConcernCasError(
                    error="write_intent_material_mismatch",
                    http_status=422,
                    payload={"error": "write_intent_material_mismatch"},
                )
            conn.rollback()
            return {
                "concern_id": concern_id,
                "seq": int(existing[3]),
                "record_ref": str(existing[1]),
                "revision": int(existing[2]),
                "write_intent_id": str(wi),
                "replayed": True,
                "fencing_generation": _read_head_row(conn, concern_id)[1],
                "through_seq": contiguous_through_seq(conn, concern_id),
            }

        resolve_authorized_by(conn, authorized_by)
        head_rev, prev_fence, _prev_ref = _read_head_row(conn, concern_id)
        if exp != head_rev:
            conn.rollback()
            raise ConcernCasError(
                error="revision_conflict",
                http_status=409,
                payload={
                    "error": "revision_conflict",
                    "current_head": _head_snapshot(conn, concern_id),
                },
            )

        new_revision = head_rev + 1
        fencing_generation = prev_fence
        if "fencing_generation" in body:
            requested = int(body["fencing_generation"])
            if requested < prev_fence:
                raise TransitionRejectedError("stale fencing_generation")
            fencing_generation = max(prev_fence, requested)

        seq = _allocate_seq(conn, concern_id)
        now = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO concern_write_intents "
            "(concern_id, write_intent_id, material_fingerprint, result_record_ref, "
            "result_revision, result_seq) VALUES (?, ?, ?, ?, ?, ?)",
            (concern_id, str(wi), fp, str(rec), new_revision, seq),
        )
        maybe_after_write_intent_insert()

        journal_payload = {**body, "material": material}
        conn.execute(
            "INSERT INTO concern_journal "
            "(concern_id, seq, record_ref, kind, payload, authorized_by, "
            "fencing_generation, revision, fold_version, schema_version, committed_at, "
            "write_intent_id, material_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                concern_id,
                seq,
                str(rec),
                "position_head",
                json_encode(journal_payload),
                authorized_by,
                fencing_generation,
                new_revision,
                FOLD_VERSION,
                SCHEMA_VERSION,
                None,
                str(wi),
                fp,
            ),
        )
        maybe_after_journal_insert()

        _apply_transition_to_views(
            conn,
            concern_id=concern_id,
            kind="position_head",
            payload=journal_payload,
            record_ref=RecordRef(str(rec)),
            intent_id=None,
            revision=new_revision,
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
                "kind": "position_head",
                "seq": seq,
                "record_ref": str(rec),
            },
        )
        conn.commit()
    except ConcernCasError:
        raise
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        msg = str(exc).lower()
        if "record_ref" in msg or "unique" in msg:
            raise ConcernCasError(
                error="record_ref_conflict",
                http_status=409,
                payload={"error": "record_ref_conflict"},
            ) from exc
        raise
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
        "revision": new_revision,
        "write_intent_id": str(wi),
        "replayed": False,
        "fencing_generation": fencing_generation,
        "through_seq": through,
    }
