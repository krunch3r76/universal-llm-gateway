"""Commit-to-activation history and current attribution projection."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .db import open_ledger_db
from .propagation_code_ref_mint import require_resolvable_code_ref
from .propagation_liveness import observe_code_ref_live

_TERMINAL_OUTCOMES = frozenset(
    {"validated", "unvalidated_timeout", "contradicted", "superseded"}
)
_SUPERSEDED_PREFIX = "superseded_by:"


@dataclass(frozen=True)
class PropagationValidation:
    """One restart-bound activation observation, not a liveness oracle."""

    validation_id: str
    row_id: str | None
    service: str
    code_ref: str
    restart_intent: str | None
    restart_boundary_monotonic: float | None
    pre_observation: dict[str, Any] | None
    post_observation: dict[str, Any] | None
    observed_code_version: str | None
    code_ref_relation: str | None
    identity_measurement: str | None
    outcome: str
    failure_reason: str | None
    kill_boundary_at: str | None
    boundary_source: str | None
    created_at: float
    updated_at: float


def _store_code_ref(code_ref: str, *, service: str) -> str:
    return require_resolvable_code_ref(code_ref, service=service)


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
    stored_ref = _store_code_ref(code_ref, service=service)
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


def get_validation(validation_id: str, *, conn=None) -> PropagationValidation | None:
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            "SELECT * FROM propagation_validation WHERE validation_id=?",
            (validation_id,),
        ).fetchone()
        return _from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def latest_validation(
    service: str, code_ref: str, *, conn=None
) -> PropagationValidation | None:
    stored = _store_code_ref(code_ref, service=service)
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
        return _from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def latest_validation_for_intent(
    restart_intent: str, *, conn=None
) -> PropagationValidation | None:
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
        return _from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def pending_validations(*, conn=None) -> list[PropagationValidation]:
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        rows = db.execute(
            "SELECT * FROM propagation_validation WHERE outcome='pending' ORDER BY created_at"
        ).fetchall()
        return [_from_row(row) for row in rows]
    finally:
        if own_conn:
            db.close()


def pending_validation_for_row(row_id: str, *, conn=None) -> PropagationValidation | None:
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
        return _from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def pending_unbound_validation_for_ref(
    service: str, code_ref: str, *, conn=None
) -> PropagationValidation | None:
    stored = _store_code_ref(code_ref, service=service)
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
        return _from_row(row) if row is not None else None
    finally:
        if own_conn:
            db.close()


def bind_validation_to_row(validation_id: str, row_id: str, *, conn=None) -> int:
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


def set_kill_boundary(
    validation_id: str,
    *,
    kill_boundary_at: str,
    boundary_source: str,
    restart_boundary_monotonic: float | None = None,
    conn=None,
) -> int:
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


def mint_pending_validation_for_intent(
    intent: Any,
    *,
    code_ref: str = "HEAD",
    advance_intent_fn=None,
) -> str:
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        probe_process_live,
    )

    service = str(intent.service)
    resolved = _store_code_ref(code_ref, service=service)
    pre = probe_process_live(service)
    pre_obs = pre if pre is not None else {"probe_reachable": False}
    now = time.time()
    conn = open_ledger_db()
    try:
        existing = conn.execute(
            """
            SELECT validation_id FROM propagation_validation
            WHERE service=? AND code_ref=? AND outcome='pending'
            """,
            (service, resolved),
        ).fetchone()
        if existing is not None:
            return str(existing["validation_id"])
        if advance_intent_fn is not None:
            old = conn.execute(
                """
                SELECT validation_id, restart_intent FROM propagation_validation
                WHERE service=? AND outcome='pending'
                ORDER BY created_at DESC LIMIT 1
                """,
                (service,),
            ).fetchone()
            if old and old["restart_intent"] and old["restart_intent"] != intent.intent_id:
                advance_intent_fn(
                    str(old["restart_intent"]),
                    from_status="verifying_activation",
                    to_status="activation_unverified",
                    reason=f"superseded_by:{intent.intent_id}",
                )
                advance_validation(
                    str(old["validation_id"]),
                    outcome="superseded",
                    failure_reason=f"superseded_by:{intent.intent_id}",
                    conn=conn,
                )
        validation_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO propagation_validation (
              validation_id, row_id, service, code_ref, restart_intent,
              pre_observation, outcome, created_at, updated_at
            ) VALUES (?, NULL, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                validation_id,
                service,
                resolved,
                str(intent.intent_id),
                json.dumps(pre_obs),
                now,
                now,
            ),
        )
        conn.commit()
        return validation_id
    finally:
        conn.close()


def sweep_stale_pending_validations(
    *, now: float | None = None, max_age_s: float = 3600, conn=None
) -> list[str]:
    ts = now if now is not None else time.time()
    own_conn = conn is None
    db = conn or open_ledger_db()
    swept: list[str] = []
    try:
        rows = db.execute(
            "SELECT validation_id, created_at FROM propagation_validation WHERE outcome='pending'"
        ).fetchall()
        for row in rows:
            if ts - float(row["created_at"]) >= max_age_s:
                if advance_validation(str(row["validation_id"]), outcome="unvalidated_timeout", conn=db):
                    swept.append(str(row["validation_id"]))
        if own_conn:
            db.commit()
    finally:
        if own_conn:
            db.close()
    return swept


def repair_supersession_pairs(*, store=None, conn=None) -> None:
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        if store is None:
            return
        for vrow in db.execute(
            "SELECT validation_id, restart_intent FROM propagation_validation WHERE outcome='pending'"
        ):
            intent_id = vrow["restart_intent"]
            if not intent_id:
                continue
            intent = store.get(str(intent_id))
            if intent is None:
                continue
            reason = intent.reason or ""
            if intent.status == "activation_unverified" and reason.startswith(_SUPERSEDED_PREFIX):
                advance_validation(
                    str(vrow["validation_id"]),
                    outcome="superseded",
                    failure_reason=reason,
                    conn=db,
                )
        for srow in db.execute(
            "SELECT validation_id, restart_intent, failure_reason FROM propagation_validation WHERE outcome='superseded'"
        ):
            old_intent = srow["restart_intent"]
            if not old_intent:
                continue
            intent = store.get(str(old_intent))
            if intent is not None and intent.status == "verifying_activation":
                reason = srow["failure_reason"] or "superseded_by:unknown"
                store.advance_if_status(
                    str(old_intent),
                    from_status="verifying_activation",
                    to_status="activation_unverified",
                    reason=reason,
                )
        if own_conn:
            db.commit()
    finally:
        if own_conn:
            db.close()


def apply_close_validation(
    *,
    conn: sqlite3.Connection,
    service: str,
    code_ref: str,
    row_id: str,
    restart_intent: str | None = None,
    restart_boundary_monotonic: float | None = None,
    post_observation: dict[str, Any] | None = None,
    observed_code_version: str | None = None,
    code_ref_relation: str | None = None,
    identity_measurement: str | None = None,
) -> None:
    stored = _store_code_ref(code_ref, service=service)
    pending = conn.execute(
        """
        SELECT validation_id FROM propagation_validation
        WHERE outcome='pending' AND (
          restart_intent=? OR row_id=? OR (service=? AND code_ref=?)
        )
        ORDER BY created_at DESC LIMIT 1
        """,
        (restart_intent, row_id, service, stored),
    ).fetchone()
    if pending is not None:
        advance_validation(
            str(pending["validation_id"]),
            outcome="validated",
            post_observation=post_observation,
            observed_code_version=observed_code_version,
            code_ref_relation=code_ref_relation,
            identity_measurement=identity_measurement,
            conn=conn,
        )
        return
    record_validation(
        service=service,
        code_ref=stored,
        row_id=row_id,
        restart_intent=restart_intent,
        restart_boundary_monotonic=restart_boundary_monotonic,
        post_observation=post_observation,
        observed_code_version=observed_code_version,
        code_ref_relation=code_ref_relation,
        identity_measurement=identity_measurement,
        outcome="validated",
        conn=conn,
    )


def current_validation(service: str, code_ref: str) -> dict[str, Any]:
    live = observe_code_ref_live(service, code_ref)
    stored = _store_code_ref(code_ref, service=service)
    record = latest_validation(service, stored)
    if live.answer == "unknown":
        verdict = "unknown"
    elif live.answer == "no":
        verdict = "not_running_committed_code"
    elif record is None:
        verdict = "activation_unattributed"
    elif record.outcome == "pending":
        verdict = "activation_pending"
    elif record.outcome == "validated" and record.identity_measurement in {"changed", "measured"}:
        verdict = "running_committed_code"
    elif record.outcome in {"unvalidated_timeout", "contradicted", "superseded"}:
        verdict = "activation_unverified"
    else:
        verdict = "activation_pending"
    return {
        "verdict": verdict,
        "liveness": {
            "answer": live.answer,
            "observed_code_version": live.observed_code_version,
            "relation": live.relation,
            "observation": live.observation,
            "reason": live.reason,
        },
        "activation": _as_dict(record) if record else None,
    }


def _from_row(row) -> PropagationValidation:
    def parse(value):
        return json.loads(value) if value else None

    keys = row.keys()
    return PropagationValidation(
        validation_id=str(row["validation_id"]),
        row_id=row["row_id"],
        service=str(row["service"]),
        code_ref=str(row["code_ref"]),
        restart_intent=row["restart_intent"],
        restart_boundary_monotonic=row["restart_boundary_monotonic"],
        pre_observation=parse(row["pre_observation"]),
        post_observation=parse(row["post_observation"]),
        observed_code_version=row["observed_code_version"],
        code_ref_relation=row["code_ref_relation"],
        identity_measurement=row["identity_measurement"],
        outcome=str(row["outcome"]),
        failure_reason=row["failure_reason"],
        kill_boundary_at=row["kill_boundary_at"] if "kill_boundary_at" in keys else None,
        boundary_source=row["boundary_source"] if "boundary_source" in keys else None,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _as_dict(record: PropagationValidation | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in {"pre_observation", "post_observation"}
    } | {
        "pre_observation": record.pre_observation,
        "post_observation": record.post_observation,
    }


__all__ = [
    "PropagationValidation",
    "advance_validation",
    "apply_close_validation",
    "bind_validation_to_row",
    "current_validation",
    "get_validation",
    "latest_validation",
    "latest_validation_for_intent",
    "mint_pending_validation_for_intent",
    "pending_unbound_validation_for_ref",
    "pending_validation_for_row",
    "pending_validations",
    "record_validation",
    "repair_supersession_pairs",
    "set_kill_boundary",
    "sweep_stale_pending_validations",
]
