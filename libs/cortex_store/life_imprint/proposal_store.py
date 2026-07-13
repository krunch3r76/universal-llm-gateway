"""Short-lived imprint proposal ledger — mint on propose, claim on commit."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import cortex_conn, json_decode, json_encode

IMPRINT_PROPOSAL_TTL_SECONDS = 900

_STATUS_OPEN = "open"
_STATUS_COMMITTED = "committed"


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _expires_iso() -> str:
    return (
        datetime.now(UTC) + timedelta(seconds=IMPRINT_PROPOSAL_TTL_SECONDS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(row: dict[str, Any]) -> bool:
    expires_at = row.get("expires_at")
    if not expires_at:
        return True
    return expires_at <= _now_iso()


def _decode_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["normalized_patch"] = json_decode(out.get("normalized_patch") or "{}")
    out["op_plan"] = json_decode(out.get("op_plan") or "[]")
    out["rejects"] = json_decode(out.get("rejects") or "[]")
    out["candidates"] = json_decode(out.get("candidates") or "[]")
    return out


def create_proposal(
    *,
    normalized_patch: dict[str, Any],
    op_plan: list[dict[str, Any]],
    rejects: list[Any] | None = None,
    candidates: list[Any] | None = None,
) -> str:
    """Persist a commit-eligible proposal; returns a new UUID4 id."""
    proposal_id = str(uuid.uuid4())
    now = _now_iso()
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO imprint_proposals "
            "(id, normalized_patch, op_plan, rejects, candidates, status, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                json_encode(normalized_patch),
                json_encode(op_plan),
                json_encode(rejects or []),
                json_encode(candidates or []),
                _STATUS_OPEN,
                now,
                _expires_iso(),
            ),
        )
        conn.commit()
    return proposal_id


def get_proposal(conn: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM imprint_proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def commit_reject_code(row: dict[str, Any] | None) -> str | None:
    """Return a typed reject code for a non-applicable proposal row."""
    if row is None:
        return "unknown_proposal"
    if row.get("status") == _STATUS_COMMITTED:
        return "proposal_already_committed"
    if is_expired(row):
        return "proposal_expired"
    rejects = row.get("rejects") or []
    candidates = row.get("candidates") or []
    op_plan = row.get("op_plan") or []
    if rejects or candidates or not op_plan:
        return "proposal_not_committable"
    if row.get("status") != _STATUS_OPEN:
        return "proposal_not_committable"
    return None


def mark_committed(conn: sqlite3.Connection, proposal_id: str) -> bool:
    """CAS open→committed inside the caller's transaction."""
    now = _now_iso()
    cur = conn.execute(
        "UPDATE imprint_proposals SET status = ?, committed_at = ? "
        "WHERE id = ? AND status = ?",
        (_STATUS_COMMITTED, now, proposal_id, _STATUS_OPEN),
    )
    return cur.rowcount > 0


__all__ = [
    "IMPRINT_PROPOSAL_TTL_SECONDS",
    "commit_reject_code",
    "create_proposal",
    "get_proposal",
    "is_expired",
    "mark_committed",
]
