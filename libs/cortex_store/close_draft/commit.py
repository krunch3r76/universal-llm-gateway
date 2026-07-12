"""Close draft commit — CAS + atomic session_close boundary."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from ..db import cortex_conn
from ..events_close import close_commit_completed
from ..models import SessionCloseRequest
from ..routes.reflective_journal import _insert_reflective_entry_tx
from ..routes.session_journals import _close_session_impl
from .store import commit_draft_cas, get_draft
from .validate import resolve_draft_paths


def _persist_reflections(
    conn: object,
    *,
    agent: str,
    session_id: str,
    reflections: list[dict[str, Any]],
) -> int:
    count = 0
    for item in reflections:
        if not isinstance(item, dict):
            continue
        entry = str(item.get("entry") or "")
        if not entry.strip():
            continue
        consolidation = item.get("consolidation_data")
        _insert_reflective_entry_tx(
            conn,
            agent=agent,
            register=str(item.get("register") or "default"),
            entry=entry,
            kind=str(item.get("kind") or "reflection"),
            session_id=session_id,
            consolidation_data_json=(
                json.dumps(consolidation) if consolidation is not None else None
            ),
        )
        count += 1
    return count


def _fields_to_close_payload(
    session_id: str,
    agent: str,
    fields: dict[str, Any],
) -> dict[str, Any]:
    depth = fields.get("depth") or "light"
    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "summary": fields.get("summary") or "",
        "session_summary_md": fields.get("session_summary_md") or "",
        "transcript_depth": depth,
    }
    for key in (
        "session_summary_md_path",
        "domains",
        "decisions",
        "open_items",
        "entity_ids",
        "prior_session_id",
        "handoff_source_path",
        "assistant_label",
        "source_ref",
    ):
        if fields.get(key) is not None:
            body[key] = fields[key]
    handoff = fields.get("handoff")
    if handoff:
        body["handoff_prompt"] = str(handoff)
    if depth == "verbatim" and fields.get("_transcript_md_resolved"):
        body["transcript_md"] = fields["_transcript_md_resolved"]
    return SessionCloseRequest.model_validate(body).model_dump(mode="json")


def _gate_checked_revision(draft: dict[str, Any], checked_revision: int) -> dict[str, Any] | None:
    if draft.get("committed_at"):
        return None
    check = draft.get("check_state") or {}
    if check.get("status") != "PASS":
        return {
            "error": "draft not checked to PASS",
            "reason": "stale_or_unchecked_revision",
            "status_code": 422,
        }
    if int(check.get("checked_revision") or -1) != checked_revision:
        return {
            "error": "checked revision mismatch",
            "reason": "stale_or_unchecked_revision",
            "status_code": 422,
        }
    if int(draft["revision"]) != checked_revision:
        return {
            "error": "draft revision changed since check",
            "reason": "stale_or_unchecked_revision",
            "status_code": 422,
        }
    return None


def execute_commit(*, session_id: str, checked_revision: int) -> dict[str, Any]:
    with cortex_conn() as conn:
        draft = get_draft(conn, session_id)
        if draft is None:
            return {
                "error": "draft not found",
                "reason": "close_draft.not_found",
                "status_code": 404,
            }
        if draft.get("committed_at"):
            return _already_closed_response(session_id, draft)

        gate_err = _gate_checked_revision(draft, checked_revision)
        if gate_err:
            return gate_err

        if not commit_draft_cas(
            conn, session_id=session_id, checked_revision=checked_revision
        ):
            return {
                "error": "commit CAS failed — revision stale",
                "reason": "stale_or_unchecked_revision",
                "status_code": 422,
            }

        fields, _ = resolve_draft_paths(draft["fields"])
        reflections = fields.get("reflections") or []
        agent = str(draft["agent"])
        if isinstance(reflections, list) and reflections:
            _persist_reflections(
                conn,
                agent=agent,
                session_id=session_id,
                reflections=reflections,
            )
        conn.commit()

    try:
        result = _close_session_impl(
            _fields_to_close_payload(session_id, agent, fields)
        )
    except HTTPException as exc:
        with cortex_conn() as conn:
            conn.execute(
                "UPDATE close_drafts SET committed_at = NULL "
                "WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        return {
            "error": str(exc.detail),
            "reason": "session_close.rejected",
            "status_code": 422,
        }

    if "error" in result:
        with cortex_conn() as conn:
            conn.execute(
                "UPDATE close_drafts SET committed_at = NULL WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        return result

    close_commit_completed(
        session_id=session_id,
        agent=agent,
        journal_row_id=int(result.get("journal_row_id") or 0),
        transcript_depth=str(result.get("transcript_depth") or "light"),
    )
    result["stop"] = {
        "session_id": result.get("session_id"),
        "journal_row_id": result.get("journal_row_id"),
        "depth": result.get("transcript_depth"),
        "debrief_turn": result.get("debrief_turn_number"),
        "content_hash": result.get("content_hash"),
    }
    return result


def _already_closed_response(session_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT id, agent FROM session_journals WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    payload: dict[str, Any] = {
        "already_closed": True,
        "session_id": session_id,
    }
    if row:
        payload["journal_row_id"] = row["id"]
        payload["agent"] = draft.get("agent") or row["agent"]
        payload["stop"] = {
            "session_id": session_id,
            "journal_row_id": row["id"],
        }
    return payload
