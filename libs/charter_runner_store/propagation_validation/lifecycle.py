"""Mint, sweep, and repair lifecycle for propagation validations."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ..db import open_ledger_db
from .model import _SUPERSEDED_PREFIX, store_code_ref
from .records import advance_validation


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
    resolved = store_code_ref(code_ref, service=service)
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


__all__ = [
    "mint_pending_validation_for_intent",
    "repair_supersession_pairs",
    "sweep_stale_pending_validations",
]
