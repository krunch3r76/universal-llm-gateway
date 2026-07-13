"""Short-lived imprint proposal ledger — mint on propose, claim on commit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import cortex_conn, json_decode, json_encode

IMPRINT_PROPOSAL_TTL_SECONDS = 900
IMPRINT_REMEMBER_DEDUPE_SECONDS = 900

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
    out["applied_result"] = json_decode(out.get("applied_result") or "[]")
    return out


def patch_sha256(normalized_patch: dict[str, Any]) -> str:
    """Canonical JSON sha256 over normalized_patch (sorted keys, compact)."""
    canonical = json.dumps(normalized_patch, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _dedupe_cutoff_iso(window_seconds: int) -> str:
    return (
        datetime.now(UTC) - timedelta(seconds=window_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_committed_by_patch_hash(
    patch_hash: str,
    window_seconds: int,
) -> dict[str, Any] | None:
    """Return a recently committed proposal with the same patch hash."""
    cutoff = _dedupe_cutoff_iso(window_seconds)
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT * FROM imprint_proposals "
            "WHERE patch_sha256 = ? AND status = ? AND committed_at >= ? "
            "ORDER BY committed_at DESC LIMIT 1",
            (patch_hash, _STATUS_COMMITTED, cutoff),
        ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def find_open_by_patch_hash(patch_hash: str) -> dict[str, Any] | None:
    """Return an in-flight open proposal for concurrent collapse."""
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT * FROM imprint_proposals "
            "WHERE patch_sha256 = ? AND status = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (patch_hash, _STATUS_OPEN),
        ).fetchone()
    if row is None:
        return None
    return _decode_row(row)


def create_proposal(
    *,
    normalized_patch: dict[str, Any],
    op_plan: list[dict[str, Any]],
    rejects: list[Any] | None = None,
    candidates: list[Any] | None = None,
    patch_hash: str | None = None,
) -> str:
    """Persist a commit-eligible proposal; returns a new UUID4 id."""
    proposal_id = str(uuid.uuid4())
    now = _now_iso()
    resolved_hash = patch_hash or patch_sha256(normalized_patch)
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO imprint_proposals "
            "(id, normalized_patch, op_plan, rejects, candidates, status, "
            "created_at, expires_at, patch_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                json_encode(normalized_patch),
                json_encode(op_plan),
                json_encode(rejects or []),
                json_encode(candidates or []),
                _STATUS_OPEN,
                now,
                _expires_iso(),
                resolved_hash,
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


def mark_committed(
    conn: sqlite3.Connection,
    proposal_id: str,
    *,
    applied: list[Any] | None = None,
) -> bool:
    """CAS open→committed inside the caller's transaction."""
    now = _now_iso()
    if applied is not None:
        cur = conn.execute(
            "UPDATE imprint_proposals SET status = ?, committed_at = ?, "
            "applied_result = ? WHERE id = ? AND status = ?",
            (
                _STATUS_COMMITTED,
                now,
                json_encode(applied),
                proposal_id,
                _STATUS_OPEN,
            ),
        )
    else:
        cur = conn.execute(
            "UPDATE imprint_proposals SET status = ?, committed_at = ? "
            "WHERE id = ? AND status = ?",
            (_STATUS_COMMITTED, now, proposal_id, _STATUS_OPEN),
        )
    return cur.rowcount > 0


__all__ = [
    "IMPRINT_PROPOSAL_TTL_SECONDS",
    "IMPRINT_REMEMBER_DEDUPE_SECONDS",
    "commit_reject_code",
    "create_proposal",
    "find_committed_by_patch_hash",
    "find_open_by_patch_hash",
    "get_proposal",
    "is_expired",
    "mark_committed",
    "patch_sha256",
]
