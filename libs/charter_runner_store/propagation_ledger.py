"""Durable open propagation rows — harvest-age tracking and proof closure."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any

from deploy_identity.code_version import normalize_code_ref
from implement_admission.propagation_row import (
    PropagationRow,
    proof_claims_performed_ancestry,
)

from .db import execute_with_retry, open_ledger_db

# Open-row proof is always an unperformed obligation (row-17 bind B).
PROOF_KIND_OBLIGATION = "obligation"


class PerformedAncestryProofError(ValueError):
    """Raised when an open-row mint attempts to persist a completed-ancestry claim."""


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
    settle_boundary_monotonic: float | None
    proof: str = ""
    proof_kind: str = PROOF_KIND_OBLIGATION
    consumption_token: str | None = None
    consumption_claimed_at: float | None = None


def _row_key(row: PropagationRow) -> str:
    """Stable identity for one obligation *attempt* (event), not current liveness.

    The ``service:code_ref:action`` shape reads like a state key, but the row
    stores one harvest attempt with an outcome. Terminal ``failed``/``closed``
    values stay frozen by design — ask :func:`observe_code_ref_live` for
    whether that ``code_ref`` is live now (F4 / obligation ≠ liveness).
    """
    return f"{row.service}:{normalize_code_ref(row.code_ref)}:{row.action}"


DEFER_HARVEST_WANTED = "harvest_wanted"
STALE_CONSUMPTION_CLAIM_S = 600.0


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
    """Insert or refresh *open* obligation rows; return stable event ids.

    Mint-boundary: open-row ``proof`` is an obligation. Persisting a past-tense
    performed-ancestry claim (``ancestry satisfied``) raises
    :class:`PerformedAncestryProofError` — the check has not run at queue time.

    ``ON CONFLICT … WHERE status='open'`` refuses to overwrite terminal events.
    That freeze is correct under immutable-event semantics; it is not a
    liveness oracle — use :func:`charter_runner_store.propagation_liveness.observe_code_ref_live`.
    """
    if not rows:
        return []
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    row_ids: list[str] = []
    try:
        for raw in rows:
            row = _mint_row(raw)
            if proof_claims_performed_ancestry(row.proof):
                raise PerformedAncestryProofError(
                    "open-row proof must be an obligation, not a performed check; "
                    f"refusing proof containing 'ancestry satisfied' for "
                    f"service={row.service!r} code_ref={row.code_ref!r}"
                )
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
                   mint_thread, mint_turn, defer_reason, proof_class, hazard, reason,
                   settle_boundary_monotonic, proof, consumption_token,
                   consumption_claimed_at
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
                settle_boundary_monotonic=row["settle_boundary_monotonic"],
                proof=str(row["proof"] or ""),
                proof_kind=PROOF_KIND_OBLIGATION,
                consumption_token=row["consumption_token"],
                consumption_claimed_at=row["consumption_claimed_at"],
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


def set_proof_class(
    row_id: str,
    proof_class: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upgrade an open row's proof class (e.g. process_live → served_artifact)."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET proof_class = ?, updated_at = ?
            WHERE row_id = ? AND status='open'
            """,
            (proof_class, now, row_id),
        )
    finally:
        if own_conn:
            db.close()


def set_settle_boundary(
    row_id: str,
    settle_boundary_monotonic: float,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Persist the drain/restart boundary used when this row was deferred."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET settle_boundary_monotonic = ?, updated_at = ?
            WHERE row_id = ? AND status='open'
            """,
            (settle_boundary_monotonic, now, row_id),
        )
    finally:
        if own_conn:
            db.close()


def reopen_failed_row(
    row_id: str,
    *,
    reason: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Revert a wrongly failed row to open — clears terminal proof, keeps history in reason."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET status='open',
                proof_payload=NULL,
                closed_at=NULL,
                defer_reason=?,
                updated_at=?
            WHERE row_id=? AND status='failed'
            """,
            (reason, now, row_id),
        )
        return cur.rowcount > 0
    finally:
        if own_conn:
            db.close()


def reopen_closed_row(
    row_id: str,
    *,
    reason: str,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Revert a wrongly closed row to open — clears terminal proof, keeps history in reason."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET status='open',
                proof_payload=NULL,
                closed_at=NULL,
                defer_reason=?,
                updated_at=?
            WHERE row_id=? AND status='closed'
            """,
            (reason, now, row_id),
        )
        return cur.rowcount > 0
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
    """Freeze an open obligation attempt as a failed *event* (immutable).

    Records the probe snapshot that contradicted the owed ``code_ref`` at that
    instant. ``status=failed`` must not be read as durable current not-live —
    a later process may serve the same SHA. Liveness questions go through
    :func:`charter_runner_store.propagation_liveness.observe_code_ref_live`.
    """
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
            "proof": row.proof,
            "proof_kind": row.proof_kind,
        }
        for row in list_open_rows(conn=conn)
    ]


def set_open_proof_payload(
    row_id: str,
    *,
    proof_payload: dict[str, Any],
    defer_reason: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Attach an observation payload to an open row without claiming closure.

    Used when a probe is recorded but the row stays open (defer/unsettled).
    Does not rewrite ``code_ref`` or mint ``proof`` obligation text.
    """
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        if defer_reason is None:
            execute_with_retry(
                db,
                """
                UPDATE propagation_ledger
                SET proof_payload=?, updated_at=?
                WHERE row_id=? AND status='open'
                """,
                (json.dumps(proof_payload), now, row_id),
            )
        else:
            execute_with_retry(
                db,
                """
                UPDATE propagation_ledger
                SET proof_payload=?, defer_reason=?, updated_at=?
                WHERE row_id=? AND status='open'
                """,
                (json.dumps(proof_payload), defer_reason, now, row_id),
            )
    finally:
        if own_conn:
            db.close()


def mint_row_id() -> str:
    """Return a unique row id when no natural key exists — test helper."""
    return str(uuid.uuid4())


def mark_harvest_wanted(
    row_id: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Persist harvest-wanted marker on an open row — charter tick will consume."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET defer_reason = ?, updated_at = ?
            WHERE row_id = ? AND status = 'open'
            """,
            (DEFER_HARVEST_WANTED, now, row_id),
        )
        return cur.rowcount > 0
    finally:
        if own_conn:
            db.close()


def list_harvest_wanted_rows(
    *,
    conn: sqlite3.Connection | None = None,
) -> list[OpenPropagationProjection]:
    """Open rows waiting for between-window charter tick consumption."""
    return [
        row
        for row in list_open_rows(conn=conn)
        if row.defer_reason == DEFER_HARVEST_WANTED
    ]


def reclaim_stale_consumption_claims(
    *,
    stale_after_s: float = STALE_CONSUMPTION_CLAIM_S,
    conn: sqlite3.Connection | None = None,
) -> int:
    """Release claims held by a crashed consumer — row stays open, not dropped."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    cutoff = time.time() - stale_after_s
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET consumption_token = NULL,
                consumption_claimed_at = NULL,
                defer_reason = ?,
                updated_at = ?
            WHERE status = 'open'
              AND consumption_token IS NOT NULL
              AND consumption_claimed_at IS NOT NULL
              AND consumption_claimed_at < ?
            """,
            (DEFER_HARVEST_WANTED, now, cutoff),
        )
        return int(cur.rowcount)
    finally:
        if own_conn:
            db.close()


def try_claim_for_consumption(
    row_id: str,
    token: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Exactly-once claim — second caller gets False, row stays open."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET consumption_token = ?,
                consumption_claimed_at = ?,
                updated_at = ?
            WHERE row_id = ?
              AND status = 'open'
              AND consumption_token IS NULL
            """,
            (token, now, now, row_id),
        )
        return cur.rowcount > 0
    finally:
        if own_conn:
            db.close()


def release_consumption_claim(
    row_id: str,
    token: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """Return an unclosed row to the harvest-wanted pool after a deferral."""
    own_conn = conn is None
    db = conn or open_ledger_db()
    now = time.time()
    try:
        cur = execute_with_retry(
            db,
            """
            UPDATE propagation_ledger
            SET consumption_token = NULL,
                consumption_claimed_at = NULL,
                defer_reason = ?,
                updated_at = ?
            WHERE row_id = ?
              AND status = 'open'
              AND consumption_token = ?
            """,
            (DEFER_HARVEST_WANTED, now, row_id, token),
        )
        return cur.rowcount > 0
    finally:
        if own_conn:
            db.close()


__all__ = [
    "DEFER_HARVEST_WANTED",
    "OpenPropagationProjection",
    "STALE_CONSUMPTION_CLAIM_S",
    "bump_age_for_open_rows",
    "close_row",
    "fail_row",
    "list_harvest_wanted_rows",
    "list_open_rows",
    "mark_harvest_wanted",
    "mint_row_id",
    "reclaim_stale_consumption_claims",
    "release_consumption_claim",
    "reopen_closed_row",
    "reopen_failed_row",
    "scoreboard_projection",
    "set_defer_reason",
    "set_proof_class",
    "set_settle_boundary",
    "try_claim_for_consumption",
    "upsert_open_rows",
]
