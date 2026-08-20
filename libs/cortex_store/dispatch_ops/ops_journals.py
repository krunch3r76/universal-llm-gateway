"""Journal and deadline ops.

Session-close ops live in ``ops_session_close``; re-exported here for
callers that imported them from this module before the split.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from durable_io.atomic import durable_write_text
from fastapi import HTTPException
from universal_logging import get_logger

from ..db import cortex_conn, execute, json_encode, query
from ..routes.assertions import _create_assertion_impl
from ..routes.deadlines import _RESOLVED_OUTCOMES, _list_deadlines_impl
from ..routes.session_journals import (
    _create_session_journal_impl,
    _list_session_journals_impl,
)
from ._shared import _FILES_ROOT, _derive_session_id_local, record
from .ops_session_close import (  # noqa: F401 — re-export for pre-split callers
    _op_session_close,
    _op_session_close_preflight,
    _op_session_handoff_upsert,
)

logger = get_logger(__name__)


def _op_deadlines(**_: object) -> dict[str, Any]:
    return _list_deadlines_impl()


def _op_deadline_resolve(
    deadline_id: str | None = None,
    resolution_note: str | None = None,
    resolved_at: str | None = None,
    evidence: str | None = None,
    fulfilling_assertion_id: int | None = None,
    outcome: str = "met",
    **_: object,
) -> dict[str, Any]:
    """Atomically close a deadline entity: write confirmed assertion + set outcome.

    ∀ deadline entity: two writes are required to stop it surfacing in
    deadlines() — a confirmed RESOLVED assertion on the deadline entity AND
    outcome in its attributes JSON. Agents historically forget the
    second write; this op performs both reliably.
    """
    if not deadline_id:
        return {"error": "deadline_id is required"}
    if not resolution_note:
        return {"error": "resolution_note is required"}
    if not resolved_at:
        return {"error": "resolved_at is required"}
    if outcome not in _RESOLVED_OUTCOMES:
        return {"error": f"outcome must be one of {sorted(_RESOLVED_OUTCOMES)}"}

    # 1. Read current deadline entity and its attributes.
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, type, attributes FROM entities WHERE id = ? AND type = 'deadline'",
            (deadline_id,),
        )
        if not rows:
            return {
                "error": f"Deadline entity not found or not type='deadline': {deadline_id}"
            }

        attrs_raw = rows[0]["attributes"]
        current_attrs: dict[str, Any] = (
            json.loads(attrs_raw) if isinstance(attrs_raw, str) and attrs_raw else {}
        )

    # 2. Write confirmed RESOLVED assertion on the deadline entity.
    assertion_body: dict[str, Any] = {
        "entity_id": deadline_id,
        "claim": f"RESOLVED — {resolution_note}",
        "confidence": "confirmed",
        "evidence": evidence or f"deadline_resolve called; resolved_at={resolved_at}",
        "derivation_type": "agent_observation",
        "observed_at": datetime.now(UTC).isoformat(),
        "confidence_score": 1.0,
    }
    if fulfilling_assertion_id is not None:
        assertion_body["fulfillment_assertion_id"] = fulfilling_assertion_id

    try:
        assertion_result = _create_assertion_impl(assertion_body)
    except HTTPException as exc:
        return {"error": f"Assertion write failed: {exc.detail}", "step": "assert"}

    resolution_assertion_id = (assertion_result.get("item") or {}).get("id")

    # 3. Merge outcome into current attributes (non-destructive merge).
    merged_attrs = {**current_attrs, "outcome": outcome}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    outcome_set = False
    try:
        with cortex_conn() as conn:
            execute(
                conn,
                "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
                (json_encode(merged_attrs), now, deadline_id),
            )
        outcome_set = True
    except sqlite3.Error as exc:
        logger.warning("deadline_resolve outcome update failed: %s", exc)
        record(
            "mcp.cortex.deadline.outcome.failed",
            deadline_id=deadline_id,
            error=str(exc),
        )

    logger.info(
        "deadline_resolve: %s — assertion=%s outcome=%s outcome_set=%s",
        deadline_id,
        resolution_assertion_id,
        outcome,
        outcome_set,
    )
    record("mcp.cortex.deadline.resolved", deadline_id=deadline_id)

    return {
        "deadline_id": deadline_id,
        "resolution_assertion_id": resolution_assertion_id,
        "outcome": outcome,
        "outcome_set": outcome_set,
    }


def _op_journal_read(
    limit: int | None = None, agent: str | None = None, **_: object
) -> dict[str, Any]:
    return _list_session_journals_impl(limit=limit or 3, agent=agent)


def _op_journal_write(
    timestamp: str | None = None,
    agent: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    file_path: str | None = None,
    session_id: str | None = None,
    prior_session_id: str | None = None,
    markdown_content: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required_fields = {"timestamp": timestamp, "agent": agent, "summary": summary}
    for field, val in required_fields.items():
        if not val:
            return {"error": f"{field} is required"}
    assert agent is not None and timestamp is not None

    derived_id = session_id or _derive_session_id_local(agent, timestamp)

    if markdown_content is not None:
        journal_path = _FILES_ROOT / "notes" / "system" / "journal" / f"{derived_id}.md"
        durable_write_text(
            journal_path, markdown_content, retain_store_root=_FILES_ROOT
        )
        logger.info("journal_write: wrote markdown to %s", journal_path)

    body: dict[str, Any] = {
        "timestamp": timestamp,
        "agent": agent,
        "summary": summary,
        **({} if domains is None else {"domains": domains}),
        **({} if decisions is None else {"decisions": decisions}),
        **({} if open_items is None else {"open_items": open_items}),
        **({} if entity_ids is None else {"entity_ids": entity_ids}),
        **({} if file_path is None else {"file_path": file_path}),
        **({} if session_id is None else {"session_id": session_id}),
        **({} if prior_session_id is None else {"prior_session_id": prior_session_id}),
    }
    result = _create_session_journal_impl(body)
    if "error" not in result:
        transcript_entity_id = result.get("transcript_entity_id", "")
        logger.info(
            "cortex journal_write: %s agent=%s transcript=%s",
            timestamp,
            agent,
            transcript_entity_id,
        )
    return result
