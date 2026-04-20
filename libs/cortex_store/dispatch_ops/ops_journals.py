"""Journal and session-close ops."""

from __future__ import annotations

import logging
from typing import Any

from ..routes.deadlines import _list_deadlines_impl
from ..routes.session_journals import (
    _close_session_impl,
    _create_session_journal_impl,
    _list_session_journals_impl,
)
from ._shared import _FILES_ROOT, _SESSION_ID_RE, _derive_session_id_local, record

logger = logging.getLogger("cortex-api.dispatch_ops.journals")


def _op_deadlines(**_: object) -> dict[str, Any]:
    return _list_deadlines_impl()


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
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        journal_path.write_text(markdown_content, encoding="utf-8")
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


def _op_session_close(
    session_id: str | None = None,
    agent: str | None = None,
    transcript_md: str | None = None,
    summary: str | None = None,
    domains: list[str] | None = None,
    decisions: list[str] | None = None,
    open_items: list[str] | None = None,
    entity_ids: list[str] | None = None,
    prior_session_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    required = {
        "session_id": session_id,
        "agent": agent,
        "transcript_md": transcript_md,
        "summary": summary,
    }
    for field, val in required.items():
        if not val:
            return {"error": f"{field} is required"}

    assert session_id and agent and transcript_md and summary

    if not _SESSION_ID_RE.match(session_id):
        return {
            "error": f"session_id {session_id!r} does not match "
            "pattern {{agent}}-YYYY-MM-DD-HHMM"
        }
    if len(summary) < 20:
        return {"error": f"summary must be >= 20 characters (got {len(summary)})"}
    if len(transcript_md) < 200:
        return {
            "error": f"transcript_md must be >= 200 characters (got {len(transcript_md)}). "
            "Stub-only closes are rejected."
        }

    has_structure = "## Turn" in transcript_md or "## Session Summary" in transcript_md
    if not has_structure:
        return {
            "error": "transcript_md must contain at least one '## Turn' heading "
            "or a '## Session Summary' section."
        }

    transcript_path = f"notes/system/transcripts/{session_id}.md"
    abs_path = _FILES_ROOT / transcript_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(transcript_md, encoding="utf-8")

    body: dict[str, Any] = {
        "session_id": session_id,
        "agent": agent,
        "transcript_md": transcript_md,
        "summary": summary,
    }
    for key, val in [
        ("domains", domains),
        ("decisions", decisions),
        ("open_items", open_items),
        ("entity_ids", entity_ids),
        ("prior_session_id", prior_session_id),
    ]:
        if val is not None:
            body[key] = val

    result = _close_session_impl(body)
    if "error" in result:
        try:
            abs_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to clean up transcript file after DB error: %s", abs_path
            )
        return result

    logger.info(
        "session_close: %s agent=%s transcript=%s",
        session_id,
        agent,
        transcript_path,
    )
    record(
        "mcp.session.close.atomic",
        agent=agent,
        session_id=session_id,
        transcript_path=transcript_path,
    )
    return result
