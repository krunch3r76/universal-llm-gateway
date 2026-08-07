"""Close draft DB layer — single write site for ``fields`` column."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ..db import cortex_conn, json_decode, json_encode
from .constants import LONG_STOP_DAYS, SHORT_TTL_DAYS, UNCOMMITTED_CAP
from .depth_defaults import default_depth_for_agent


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_ttl_iso() -> str:
    return (datetime.now(UTC) + timedelta(days=SHORT_TTL_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _long_stop_iso() -> str:
    return (datetime.now(UTC) + timedelta(days=LONG_STOP_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _has_reflections(fields: dict[str, Any]) -> bool:
    refs = fields.get("reflections")
    return isinstance(refs, list) and len(refs) > 0


def get_draft(conn: object, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT * FROM close_drafts WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["fields"] = json_decode(out.get("fields") or "{}")
    if out.get("check_state"):
        out["check_state"] = json_decode(out["check_state"])
    return out


def count_uncommitted_for_agent(conn: object, agent: str) -> int:
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT COUNT(*) AS cnt FROM close_drafts "
        "WHERE agent = ? AND committed_at IS NULL",
        (agent,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def oldest_uncommitted_drafts(conn: object, agent: str, limit: int = 5) -> list[str]:
    rows = conn.execute(  # type: ignore[union-attr]
        "SELECT session_id FROM close_drafts "
        "WHERE agent = ? AND committed_at IS NULL "
        "ORDER BY created_at ASC LIMIT ?",
        (agent, limit),
    ).fetchall()
    return [r["session_id"] for r in rows]


def create_draft(
    conn: object,
    *,
    session_id: str,
    agent: str,
    prior_session_id: str | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    fields: dict[str, Any] = {"depth": default_depth_for_agent(agent)}
    if prior_session_id:
        fields["prior_session_id"] = prior_session_id
    conn.execute(  # type: ignore[union-attr]
        "INSERT INTO close_drafts "
        "(session_id, agent, revision, fields, ttl_expires_at, "
        "created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
        (
            session_id,
            agent,
            json_encode(fields),
            _short_ttl_iso(),
            now,
            now,
        ),
    )
    return {
        "session_id": session_id,
        "agent": agent,
        "revision": 1,
        "fields": fields,
        "committed_at": None,
    }


def update_draft_fields(
    conn: object,
    *,
    session_id: str,
    patch: dict[str, Any],
) -> tuple[int, dict[str, Any]] | None:
    """Single write site for ``close_drafts.fields`` — bumps revision atomically."""
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT fields, revision, reflection_flush_after FROM close_drafts "
        "WHERE session_id = ? AND committed_at IS NULL",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    current = json_decode(row["fields"] or "{}")
    merged = {**current, **patch}
    new_revision = int(row["revision"]) + 1
    now = _now_iso()
    flush_after = row["reflection_flush_after"]
    ttl = None if _has_reflections(merged) else _short_ttl_iso()
    if _has_reflections(merged) and not flush_after:
        flush_after = _long_stop_iso()
    conn.execute(  # type: ignore[union-attr]
        "UPDATE close_drafts SET fields = ?, revision = ?, updated_at = ?, "
        "ttl_expires_at = ?, reflection_flush_after = COALESCE(?, reflection_flush_after) "
        "WHERE session_id = ? AND committed_at IS NULL",
        (
            json_encode(merged),
            new_revision,
            now,
            ttl,
            flush_after,
            session_id,
        ),
    )
    return new_revision, merged


def stamp_check_state(
    conn: object,
    *,
    session_id: str,
    checked_revision: int,
    status: str,
    report: dict[str, Any],
) -> bool:
    now = _now_iso()
    cur = conn.execute(  # type: ignore[union-attr]
        "UPDATE close_drafts SET check_state = ?, updated_at = ? "
        "WHERE session_id = ? AND committed_at IS NULL AND revision = ?",
        (
            json_encode(
                {
                    "checked_revision": checked_revision,
                    "status": status,
                    "report": report,
                }
            ),
            now,
            session_id,
            checked_revision,
        ),
    )
    return cur.rowcount > 0


def commit_draft_cas(
    conn: object,
    *,
    session_id: str,
    checked_revision: int,
) -> bool:
    """Compare-and-swap commit gate — sets committed_at when revision matches."""
    now = _now_iso()
    cur = conn.execute(  # type: ignore[union-attr]
        "UPDATE close_drafts SET committed_at = ?, updated_at = ? "
        "WHERE session_id = ? AND revision = ? AND committed_at IS NULL",
        (now, now, session_id, checked_revision),
    )
    return cur.rowcount > 0


def cap_exceeded(agent: str) -> tuple[bool, list[str]]:
    with cortex_conn() as conn:
        cnt = count_uncommitted_for_agent(conn, agent)
        if cnt < UNCOMMITTED_CAP:
            return False, []
        return True, oldest_uncommitted_drafts(conn, agent)


__all__ = [
    "UNCOMMITTED_CAP",
    "commit_draft_cas",
    "count_uncommitted_for_agent",
    "create_draft",
    "get_draft",
    "stamp_check_state",
    "update_draft_fields",
]
