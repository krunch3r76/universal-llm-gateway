"""Read and bind queries for propagation validation rows."""

from __future__ import annotations

import time

from ..db import open_ledger_db
from .model import PropagationValidation, from_row, store_code_ref


def get_validation(validation_id: str, *, conn=None) -> PropagationValidation | None:
    """Load one validation row by primary key, or None when absent."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            "SELECT * FROM propagation_validation WHERE validation_id=?",
            (validation_id,),
        ).fetchone()
        return from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def latest_validation(
    service: str, code_ref: str, *, conn=None
) -> PropagationValidation | None:
    """Return the latest validation row for one service and stored code_ref."""
    stored = store_code_ref(code_ref, service=service)
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            """
            SELECT * FROM propagation_validation
            WHERE service=? AND code_ref=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (service, stored),
        ).fetchone()
        return from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def latest_validation_for_intent(
    restart_intent: str, *, conn=None
) -> PropagationValidation | None:
    """Return the newest validation row bound to one restart intent id."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            """
            SELECT * FROM propagation_validation
            WHERE restart_intent=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (restart_intent,),
        ).fetchone()
        return from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def pending_validations(*, conn=None) -> list[PropagationValidation]:
    """List all pending validation rows ordered by creation time."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        rows = db.execute(
            "SELECT * FROM propagation_validation WHERE outcome='pending' ORDER BY created_at"
        ).fetchall()
        return [from_row(row) for row in rows]
    finally:
        if own_conn:
            db.close()


def pending_validation_for_row(row_id: str, *, conn=None) -> PropagationValidation | None:
    """Return the pending validation bound to one propagation ledger row."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            """
            SELECT * FROM propagation_validation
            WHERE row_id=? AND outcome='pending' LIMIT 1
            """,
            (row_id,),
        ).fetchone()
        return from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def pending_unbound_validation_for_ref(
    service: str, code_ref: str, *, conn=None
) -> PropagationValidation | None:
    """Return the newest pending validation with no ledger row binding yet."""
    stored = store_code_ref(code_ref, service=service)
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            """
            SELECT * FROM propagation_validation
            WHERE service=? AND code_ref=? AND outcome='pending' AND row_id IS NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (service, stored),
        ).fetchone()
        return from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def bind_validation_to_row(validation_id: str, row_id: str, *, conn=None) -> int:
    """Attach a pending validation to a propagation row; return rows updated."""
    now = time.time()
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        cursor = db.execute(
            """
            UPDATE propagation_validation
            SET row_id=?, updated_at=?
            WHERE validation_id=? AND outcome='pending'
              AND (row_id IS NULL OR row_id=?)
            """,
            (row_id, now, validation_id, row_id),
        )
        if own_conn:
            db.commit()
        return int(cursor.rowcount)
    finally:
        if own_conn:
            db.close()


def deferred_activation_bind_failure(
    activation_validation_id: object, row_id: str, *, conn=None
) -> str | None:
    """Return harvest_wanted reason when deferred activation bind cannot succeed."""
    if not activation_validation_id:
        return "manage_deferred_missing_activation_validation_id"
    if bind_validation_to_row(str(activation_validation_id), row_id, conn=conn) >= 1:
        return None
    return "activation_validation_bind_failed"


__all__ = [
    "bind_validation_to_row",
    "deferred_activation_bind_failure",
    "get_validation",
    "latest_validation",
    "latest_validation_for_intent",
    "pending_unbound_validation_for_ref",
    "pending_validation_for_row",
    "pending_validations",
]
