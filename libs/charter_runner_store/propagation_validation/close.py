"""Close-path validation and current attribution projection."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..propagation_liveness import observe_code_ref_live
from .model import as_dict, store_code_ref
from .queries import latest_validation
from .records import advance_validation, record_validation


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
    """Advance or record validated close-path attribution for one ledger row."""
    stored = store_code_ref(code_ref, service=service)
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
    """Project liveness plus latest validation into one attribution verdict."""
    live = observe_code_ref_live(service, code_ref)
    stored = store_code_ref(code_ref, service=service)
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
        "activation": as_dict(record) if record else None,
    }


__all__ = ["apply_close_validation", "current_validation"]
