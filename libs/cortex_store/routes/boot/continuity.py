"""GET /boot-continuity — last-session handoff and continuation chain for boot."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query

from ...db import cortex_conn
from ...db import query as db_query
from ._render import _table_exists

router = APIRouter(tags=["boot"])


def _decode_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if not value:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(decoded, list):
            return [str(v) for v in decoded]
    return []


def _get_session_row(conn: object, session_id: str) -> dict[str, Any] | None:
    rows = db_query(
        conn,  # type: ignore[arg-type]
        "SELECT * FROM session_journals WHERE session_id = ? LIMIT 1",
        (session_id,),
    )
    return rows[0] if rows else None


def _build_continuity_chain(conn: object, latest_session_id: str) -> list[str]:
    chain: list[str] = []
    current_id: str | None = latest_session_id
    seen: set[str] = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        row = _get_session_row(conn, current_id)
        if row is None:
            chain.append(current_id)
            break
        chain.append(row["session_id"])
        current_id = row.get("prior_session_id")
    return list(reversed(chain))


def _get_handoff(conn: object, transcript_entity_id: str) -> dict[str, Any] | None:
    if not (
        _table_exists(conn, "reflective_journal")
        and _table_exists(conn, "journal_links")
    ):
        return None
    rows = db_query(
        conn,  # type: ignore[arg-type]
        """
        SELECT rj.id AS entry_id, rj.entry AS text
        FROM journal_links jl
        JOIN reflective_journal rj ON rj.id = jl.from_entry
        WHERE jl.link_type = 'handoff_for'
          AND jl.to_entity = ?
          AND rj.kind = 'handoff'
        ORDER BY rj.id DESC
        LIMIT 1
        """,
        (transcript_entity_id,),
    )
    if not rows:
        return None
    return {"entry_id": rows[0]["entry_id"], "text": rows[0]["text"]}


def _get_sibling_continuations(
    conn: object,
    *,
    agent: str,
    prior_session_id: str | None,
    latest_session_id: str,
) -> list[str]:
    if not prior_session_id:
        return []
    rows = db_query(
        conn,  # type: ignore[arg-type]
        """
        SELECT session_id
        FROM session_journals
        WHERE agent = ?
          AND prior_session_id = ?
          AND session_id != ?
        ORDER BY id ASC
        """,
        (agent, prior_session_id, latest_session_id),
    )
    return [r["session_id"] for r in rows]


@router.get("/boot-continuity")
def get_boot_continuity(
    agent: str = Query(
        ..., description="Agent whose latest session continuity to render"
    ),
) -> dict[str, Any]:
    """Return last-session handoff state and continuation context for boot cards."""
    conn = cortex_conn()
    try:
        rows = db_query(
            conn,
            """
            SELECT id, session_id, agent, timestamp, summary, open_items, prior_session_id
            FROM session_journals
            WHERE agent = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (agent,),
        )
        if not rows:
            return {
                "last_session": None,
                "handoff": None,
                "continuity_chain": [],
                "continuations": [],
                "hints": [],
            }

        row = rows[0]
        transcript_entity_id = f"transcript:{row['session_id']}"
        handoff = _get_handoff(conn, transcript_entity_id)
        continuity_chain = _build_continuity_chain(conn, row["session_id"])
        continuations = _get_sibling_continuations(
            conn,
            agent=agent,
            prior_session_id=row.get("prior_session_id"),
            latest_session_id=row["session_id"],
        )
        hints: list[str] = []
        # Per assertion 8384 (session web-2026-05-04-1057): handoffs are
        # user-facing artifacts for manual copy-paste at end of chat, not boot
        # orientation material. Absence of a handoff is NOT a gap to surface at
        # boot — the `no_handoff_captured` hint has been retired. The
        # `prior_session_id_omitted` hint remains because it flags an actual
        # provenance gap in session_journals.
        if row.get("prior_session_id") is None and len(continuity_chain) == 1:
            earlier = db_query(
                conn,
                "SELECT 1 FROM session_journals WHERE agent = ? AND id < ? LIMIT 1",
                (agent, row["id"]),
            )
            if earlier:
                hints.append("prior_session_id_omitted")

        return {
            "last_session": {
                "session_id": row["session_id"],
                "agent": row["agent"],
                "timestamp": row["timestamp"],
                "summary": row["summary"],
                "open_items": _decode_json_list(row.get("open_items")),
                "transcript_entity_id": transcript_entity_id,
            },
            "handoff": handoff,
            "continuity_chain": continuity_chain,
            "continuations": continuations,
            "hints": hints,
        }
    finally:
        conn.close()
