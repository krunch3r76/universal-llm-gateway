"""Durable open propagation rows — harvest-age tracking and proof closure."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from deploy_identity.code_version import normalize_code_ref
from implement_admission.propagation_row import PropagationRow

from .db import execute_with_retry, open_ledger_db


@dataclass(frozen=True)
class OpenPropagationProjection:
    """Scoreboard-visible open row with harvest age."""

    row_id: str
    service: str
    code_ref: str
    safe_window: str
    age_in_harvests: int
    mint_thread: str | None
    mint_turn: int | None
    defer_reason: str | None
    proof_class: str
    hazard: str | None
    reason: str | None


def _row_key(row: PropagationRow) -> str:
    return f"{row.service}:{normalize_code_ref(row.code_ref)}:{row.action}"


def _mint_row(row: PropagationRow) -> PropagationRow:
    """Resolve symbolic code_ref (HEAD) before persistence."""
    resolved = normalize_code_ref(row.code_ref)
    if resolved == row.code_ref:
        return row
    return row.model_copy(update={"code_ref": resolved})


def upsert_open_rows(
    rows: list[PropagationRow],
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Insert or refresh open rows; return stable row ids."""
    if not rows:
        return []
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    row_ids: list[str] = []
    try:
        for raw in rows:
            row = _mint_row(raw)
            row_id = _row_key(row)
            row_ids.append(row_id)
            execute_with_retry(
                db,
                """
                INSERT INTO propagation_ledger (
                  row_id, service, action, code_ref, safe_window, hazard, reason,
                  proof, proof_class, mint_thread, mint_turn, status, age_in_harvests,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 0, ?, ?)
                ON CONFLICT(row_id) DO UPDATE SET
                  hazard=excluded.hazard,
                  reason=excluded.reason,
                  proof=excluded.proof,
                  proof_class=excluded.proof_class,
                  mint_thread=COALESCE(excluded.mint_thread, propagation_ledger.mint_thread),
                  mint_turn=COALESCE(excluded.mint_turn, propagation_ledger.mint_turn),
                  updated_at=excluded.updated_at
                WHERE propagation_ledger.status='open'
                """,
                (
                    row_id,
                    row.service,
                    row.action,
                    row.code_ref,
                    row.safe_window,
                    row.hazard,
                    row.reason,
                    row.proof,
                    row.proof_class,
                    row.mint_thread,
                    row.mint_turn,
                    now,
                    now,
                ),
            )
    finally:
        if own_conn:
            db.close()
    return row_ids


def list_open_rows(*, conn: sqlite3.Connection | None = None) -> list[OpenPropagationProjection]:
    """Return all open rows ordered by age then service."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        cur = db.execute(
            """
            SELECT row_id, service, code_ref, safe_window, age_in_harvests,
                   mint_thread, mint_turn, defer_reason, proof_class, hazard, reason
            FROM propagation_ledger
            WHERE status='open'
            ORDER BY age_in_harvests DESC, service ASC
            """
        )
        return [
            OpenPropagationProjection(
                row_id=str(row["row_id"]),
                service=str(row["service"]),
                code_ref=str(row["code_ref"]),
                safe_window=str(row["safe_window"]),
                age_in_harvests=int(row["age_in_harvests"]),
                mint_thread=row["mint_thread"],
                mint_turn=row["mint_turn"],
                defer_reason=row["defer_reason"],
                proof_class=str(row["proof_class"]),
                hazard=row["hazard"],
                reason=row["reason"],
            )
            for row in cur.fetchall()
        ]
    finally:
        if own_conn:
            db.close()


def bump_age_for_open_rows(*, conn: sqlite3.Connection | None = None) -> None:
    """Increment harvest age for every still-open row."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET age_in_harvests = age_in_harvests + 1,
                updated_at = ?
            WHERE status='open'
            """,
            (now,),
        )
    finally:
        if own_conn:
            db.close()


def set_defer_reason(
    row_id: str,
    reason: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Record why a row did not fire this harvest pass."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET defer_reason = ?, updated_at = ?
            WHERE row_id = ? AND status='open'
            """,
            (reason, now, row_id),
        )
    finally:
        if own_conn:
            db.close()


def fail_row(
    row_id: str,
    *,
    proof_payload: dict[str, Any],
    reason: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Mark a row failed after observed proof mismatch — not on restart status alone."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    payload = {**proof_payload, "failure_reason": reason}
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET status='failed',
                proof_payload=?,
                closed_at=?,
                defer_reason=?,
                updated_at=?
            WHERE row_id=? AND status='open'
            """,
            (json.dumps(payload), now, reason, now, row_id),
        )
    finally:
        if own_conn:
            db.close()


def close_row(
    row_id: str,
    *,
    proof_payload: dict[str, Any],
    conn: sqlite3.Connection | None = None,
) -> None:
    """Close a row after observed proof — never on restart status alone."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET status='closed',
                proof_payload=?,
                closed_at=?,
                defer_reason=NULL,
                updated_at=?
            WHERE row_id=? AND status='open'
            """,
            (json.dumps(proof_payload), now, now, row_id),
        )
    finally:
        if own_conn:
            db.close()


def scoreboard_projection(*, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Addressable open-row list for operator scoreboard surfaces."""
    return [
        {
            "service": row.service,
            "code_ref": row.code_ref,
            "safe_window": row.safe_window,
            "age_in_harvests": row.age_in_harvests,
            "mint_thread": row.mint_thread,
            "mint_turn": row.mint_turn,
            "defer_reason": row.defer_reason,
            "proof_class": row.proof_class,
            "hazard": row.hazard,
        }
        for row in list_open_rows(conn=conn)
    ]


def mint_row_id() -> str:
    """Return a unique row id when no natural key exists — test helper."""
    return str(uuid.uuid4())


__all__ = [
    "OpenPropagationProjection",
    "bump_age_for_open_rows",
    "close_row",
    "fail_row",
    "list_open_rows",
    "mint_row_id",
    "scoreboard_projection",
    "set_defer_reason",
    "upsert_open_rows",
]
