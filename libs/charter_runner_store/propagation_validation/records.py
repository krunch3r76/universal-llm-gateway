"""Write paths for propagation validation rows."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from ..db import open_ledger_db
from .model import _TERMINAL_OUTCOMES, store_code_ref


def record_validation(
    *,
    service: str,
    code_ref: str,
    row_id: str | None = None,
    restart_intent: str | None = None,
    restart_boundary_monotonic: float | None = None,
    pre_observation: dict[str, Any] | None = None,
    post_observation: dict[str, Any] | None = None,
    observed_code_version: str | None = None,
    code_ref_relation: str | None = None,
    identity_measurement: str | None = None,
    outcome: str = "pending",
    failure_reason: str | None = None,
    kill_boundary_at: str | None = None,
    boundary_source: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Persist one immutable-attempt-shaped validation snapshot."""
    stored_ref = store_code_ref(code_ref, service=service)
    validation_id = str(uuid.uuid4())
    now = time.time()
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        db.execute(
            """
            INSERT INTO propagation_validation (
              validation_id, row_id, service, code_ref, restart_intent,
              restart_boundary_monotonic, pre_observation, post_observation,
              observed_code_version, code_ref_relation, identity_measurement,
              outcome, failure_reason, kill_boundary_at, boundary_source,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validation_id,
                row_id,
                service,
                stored_ref,
                restart_intent,
                restart_boundary_monotonic,
                json.dumps(pre_observation) if pre_observation else None,
                json.dumps(post_observation) if post_observation else None,
                observed_code_version,
                code_ref_relation,
                identity_measurement,
                outcome,
                failure_reason,
                kill_boundary_at,
                boundary_source,
                now,
                now,
            ),
        )
        if own_conn:
            db.commit()
    finally:
        if own_conn:
            db.close()
    return validation_id


def advance_validation(
    validation_id: str,
    *,
    outcome: str,
    post_observation: dict[str, Any] | None = None,
    observed_code_version: str | None = None,
    code_ref_relation: str | None = None,
    identity_measurement: str | None = None,
    failure_reason: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    """CAS-advance a pending validation row exactly once."""
    if outcome == "pending" or outcome not in _TERMINAL_OUTCOMES:
        raise ValueError(f"illegal validation outcome: {outcome!r}")
    now = time.time()
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        cursor = db.execute(
            """
            UPDATE propagation_validation
            SET outcome=?,
                post_observation=COALESCE(?, post_observation),
                observed_code_version=COALESCE(?, observed_code_version),
                code_ref_relation=COALESCE(?, code_ref_relation),
                identity_measurement=COALESCE(?, identity_measurement),
                failure_reason=COALESCE(?, failure_reason),
                updated_at=?
            WHERE validation_id=? AND outcome='pending'
            """,
            (
                outcome,
                json.dumps(post_observation) if post_observation else None,
                observed_code_version,
                code_ref_relation,
                identity_measurement,
                failure_reason,
                now,
                validation_id,
            ),
        )
        if own_conn:
            db.commit()
        return int(cursor.rowcount)
    finally:
        if own_conn:
            db.close()


def set_kill_boundary(
    validation_id: str,
    *,
    kill_boundary_at: str,
    boundary_source: str,
    restart_boundary_monotonic: float | None = None,
    conn=None,
) -> int:
    """Persist kill-boundary metadata on a pending validation row."""
    now = time.time()
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        cursor = db.execute(
            """
            UPDATE propagation_validation
            SET kill_boundary_at=?, boundary_source=?,
                restart_boundary_monotonic=COALESCE(?, restart_boundary_monotonic),
                updated_at=?
            WHERE validation_id=? AND outcome='pending'
            """,
            (
                kill_boundary_at,
                boundary_source,
                restart_boundary_monotonic,
                now,
                validation_id,
            ),
        )
        if own_conn:
            db.commit()
        return int(cursor.rowcount)
    finally:
        if own_conn:
            db.close()


__all__ = ["advance_validation", "record_validation", "set_kill_boundary"]
