"""Commit-to-activation history and current attribution projection."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .db import execute_with_retry, open_ledger_db
from .propagation_liveness import observe_code_ref_live


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
    created_at: float
    updated_at: float


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
) -> str:
    """Persist one immutable-attempt-shaped validation snapshot."""
    validation_id = str(uuid.uuid4())
    now = time.time()
    conn = open_ledger_db()
    try:
        execute_with_retry(
            conn,
            """
            INSERT INTO propagation_validation (
              validation_id, row_id, service, code_ref, restart_intent,
              restart_boundary_monotonic, pre_observation, post_observation,
              observed_code_version, code_ref_relation, identity_measurement,
              outcome, failure_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                validation_id,
                row_id,
                service,
                code_ref,
                restart_intent,
                restart_boundary_monotonic,
                json.dumps(pre_observation) if pre_observation else None,
                json.dumps(post_observation) if post_observation else None,
                observed_code_version,
                code_ref_relation,
                identity_measurement,
                outcome,
                failure_reason,
                now,
                now,
            ),
        )
    finally:
        conn.close()
    return validation_id


def latest_validation(
    service: str, code_ref: str, *, conn=None
) -> PropagationValidation | None:
    """Return the newest activation record for a service/ref pair."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    try:
        row = db.execute(
            """
            SELECT * FROM propagation_validation
            WHERE service=? AND code_ref=?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (service, code_ref),
        ).fetchone()
        if row is None:
            return None
        return _from_row(row)
    finally:
        if own_conn:
            db.close()


def current_validation(service: str, code_ref: str) -> dict[str, Any]:
    """Join latest activation attribution with a fresh liveness observation."""
    live = observe_code_ref_live(service, code_ref)
    record = latest_validation(service, live.code_ref)
    if live.answer == "unknown":
        verdict = "unknown"
    elif live.answer == "no":
        verdict = "not_running_committed_code"
    elif record is None:
        verdict = "activation_unattributed"
    elif record.outcome == "validated" and record.identity_measurement in {
        "changed",
        "measured",
    }:
        verdict = "running_committed_code"
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
    "current_validation",
    "latest_validation",
    "record_validation",
]
