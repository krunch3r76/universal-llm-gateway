from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, decode_row, json_encode, query
from ..dispatch_ops._shared import _FILES_ROOT, _SESSION_ID_RE, record
from ..models import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionJournalCreate,
    SessionJournalItem,
    SessionJournalList,
)
from ..session_close_validation import (
    _USER_VOICE_RE,
    _audit_normalization_refusals_for_session,
    _emit_rejected,
)
from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    compute_text_content_hash,
    resolve_jsonl_path,
)
from .reflective_journal import _insert_journal_link_tx, _insert_reflective_entry_tx

logger = get_logger("cortex-api.session_journals")
router = APIRouter(prefix="/session-journals", tags=["session-journals"])

_JSON_FIELDS = frozenset({"domains", "decisions", "open_items", "entity_ids"})


def _parse_opened_at(transcript_id: str) -> str | None:
    """Derive ``opened_at`` ISO 8601 from transcript ID (``{agent}-YYYY-MM-DD-HHMM``)."""
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})$", transcript_id)
    if match:
        y, m, d, hh, mm = match.groups()
        return f"{y}-{m}-{d}T{hh}:{mm}:00Z"
    return None


def _stamp_transcript_timestamps(
    conn: object,
    transcript_id: str,
    *,
    opened_at: str | None = None,
    closed_at: str | None = None,
) -> None:
    """Merge ``opened_at`` / ``closed_at`` into the transcript entity's attributes."""
    if not opened_at and not closed_at:
        return
    entity_id = f"transcript:{transcript_id}"
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    existing: dict[str, object] = (
        json.loads(row["attributes"]) if row and row["attributes"] else {}
    )
    if opened_at:
        existing["opened_at"] = opened_at
    if closed_at:
        existing["closed_at"] = closed_at
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
        (json_encode(existing), now, entity_id),
    )


def _derive_session_id(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + timestamp string.

    Accepts ISO 8601 (``2026-04-08T23:11:00Z``) or any string containing
    a ``YYYY-MM-DD`` date fragment.  Falls back to today if unparseable.
    Format: ``{agent}-YYYY-MM-DD-HHMM``.
    """
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):?(\d{2})", timestamp)
    if match:
        year, mon, day, hour, minute = match.groups()
        return f"{agent}-{year}-{mon}-{day}-{hour}{minute}"
    today = datetime.now(UTC).strftime("%Y-%m-%d-%H%M")
    return f"{agent}-{today}"


def _ensure_transcript_entity(
    conn: object,
    transcript_id: str,
    agent: str,
    timestamp: str,
    source_uri: str | None = None,
) -> None:
    """INSERT OR IGNORE the transcript entity — idempotent.

    When *source_uri* is provided, also updates it on an existing entity
    (INSERT OR IGNORE won't overwrite an existing row).
    """
    entity_id = f"transcript:{transcript_id}"
    if source_uri is None:
        source_uri = f"files://notes/system/transcripts/{transcript_id}.md"
    conn.execute(  # type: ignore[union-attr]
        "INSERT OR IGNORE INTO entities "
        "(id, type, name, status, source_uri, created_at, updated_at) "
        "VALUES (?, 'transcript', ?, 'confirmed', ?, ?, ?)",
        (entity_id, transcript_id, source_uri, timestamp, timestamp),
    )
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET source_uri = ?, updated_at = ? "
        "WHERE id = ? AND (source_uri IS NULL OR source_uri != ?)",
        (source_uri, timestamp, entity_id, source_uri),
    )


def _ensure_continues_edge(
    conn: object,
    session_id: str,
    prior_session_id: str,
    agent: str,
    timestamp: str,
) -> None:
    """Write a continues edge: transcript:{session_id} → transcript:{prior_session_id}.

    Checks for an existing active edge before inserting to stay idempotent.
    """
    from_node = f"transcript:{session_id}"
    to_node = f"transcript:{prior_session_id}"
    existing = conn.execute(  # type: ignore[union-attr]
        "SELECT 1 FROM session_edges "
        "WHERE from_node = ? AND to_node = ? AND edge_type = 'continues' "
        "AND valid_until IS NULL LIMIT 1",
        (from_node, to_node),
    ).fetchone()
    if existing:
        return
    conn.execute(  # type: ignore[union-attr]
        "INSERT INTO session_edges "
        "(session_id, agent, from_node, to_node, edge_type, strength, edge_source, created_at) "
        "VALUES (?, ?, ?, ?, 'continues', 0.9, 'server', ?)",
        (session_id, agent, from_node, to_node, timestamp),
    )


@router.get("", response_model=SessionJournalList)
def list_session_journals(
    agent: str | None = None,
    limit: int = Query(3, ge=1, le=100),
) -> SessionJournalList:
    """List recent session journals in reverse insertion order.

    Args:
        agent: Filter by agent name (cursor, web, api). Omit for all agents.
        limit: Maximum results (1-100, default 3).
    """
    clauses: list[str] = []
    params: list[str | int] = []
    if agent:
        clauses.append("agent = ?")
        params.append(agent)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    conn = cortex_conn()
    try:
        rows = query(
            conn,
            f"SELECT * FROM session_journals{where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
    finally:
        conn.close()

    return SessionJournalList(
        items=[SessionJournalItem(**decode_row(row, _JSON_FIELDS)) for row in rows]
    )


@router.post("", response_model=SessionJournalItem, status_code=status.HTTP_201_CREATED)
def create_session_journal(body: SessionJournalCreate) -> SessionJournalItem:
    """Create a session journal row, auto-create transcript entity, and return the item.

    Side effects (all within the same transaction):
    - transcript:{session_id} entity is created (INSERT OR IGNORE — idempotent)
    - A ``continues`` edge is written if ``prior_session_id`` is supplied
    ``session_id`` is derived from the timestamp if not explicitly provided.
    """
    transcript_id = body.session_id or _derive_session_id(body.agent, body.timestamp)
    transcript_entity_id = f"transcript:{transcript_id}"

    conn = cortex_conn()
    try:
        _ensure_transcript_entity(conn, transcript_id, body.agent, body.timestamp)
        _stamp_transcript_timestamps(
            conn,
            transcript_id,
            opened_at=_parse_opened_at(transcript_id),
            closed_at=body.timestamp,
        )

        if body.prior_session_id:
            # Ensure the prior transcript entity exists too (so the FK-like
            # reference is safe even when the prior session journal isn't present).
            _ensure_transcript_entity(
                conn, body.prior_session_id, body.agent, body.timestamp
            )
            _ensure_continues_edge(
                conn, transcript_id, body.prior_session_id, body.agent, body.timestamp
            )

        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.timestamp,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                body.file_path,
                transcript_id,
                body.prior_session_id,
            ),
        )
        conn.commit()
        rows = query(
            conn,
            "SELECT * FROM session_journals WHERE id = ?",
            (cur.lastrowid,),
        )
    finally:
        conn.close()

    if not rows:
        logger.error(
            "Session journal create succeeded but no row returned for agent=%s",
            body.agent,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Session journal created but could not be read back",
        )

    item = SessionJournalItem(**decode_row(rows[0], _JSON_FIELDS))
    item.transcript_entity_id = transcript_entity_id
    return item


def _raise_422(*, reason: str, session_id: str, agent: str, detail: str) -> None:
    """Emit `mcp.session.close.rejected` and raise the matching 422 in one call."""
    _emit_rejected(reason, session_id=session_id, agent=agent, detail=detail)
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


@router.post(
    "/close",
    response_model=SessionCloseResponse,
    status_code=status.HTTP_201_CREATED,
)
def close_session(body: SessionCloseRequest) -> SessionCloseResponse:
    """Atomic session close: assemble verbatim, write file, commit DB tx.

    The handler is the single atomic boundary:
      1. Validate ``session_id``, ``summary``, ``session_summary_md``.
      2. Resolve ``transcript_jsonl_path`` under ``CURSOR_AGENT_TRANSCRIPTS_ROOT``.
      3. Assemble the verbatim layer from the JSONL.
      4. Compose verbatim + ``session_summary_md`` into the final
         markdown.
      5. Re-validate the composed markdown (dual-layer doctrine — defense
         in depth; should always pass when assembly succeeded).
      6. Idempotency check on ``session_journals.session_id``.
      7. Write the file under ``notes/system/transcripts/{session_id}.md``.
      8. Atomic DB tx: entity + journal row + ``continues`` edge +
         optional handoff entry.
      9. Compute ``content_hash`` of the on-disk markdown and return.

    On any failure between steps 2 and 8 that occurs after the file is
    written, the file is unlinked before raising.
    On any failure after the transcript file is written, a best-effort
    ``Path.unlink`` is performed; an OSError on unlink is logged at WARNING
    and does not suppress the original exception.
    """
    if not _SESSION_ID_RE.match(body.session_id):
        _raise_422(
            reason="session_id.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                f"session_id {body.session_id!r} does not match "
                "pattern {{agent}}-YYYY-MM-DD-HHMM"
            ),
        )

    if len(body.summary) < 20:
        _raise_422(
            reason="summary.too_short",
            session_id=body.session_id,
            agent=body.agent,
            detail=f"summary must be >= 20 characters (got {len(body.summary)})",
        )

    if not body.session_summary_md.strip():
        _raise_422(
            reason="session_summary.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail="session_summary_md is required (structural layer).",
        )
    if "## Session Summary" not in body.session_summary_md:
        _raise_422(
            reason="session_summary.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                "session_summary_md must contain a '## Session Summary' heading; "
                "this is the structural layer the agent composes (decisions, "
                "files modified, continuation state)."
            ),
        )

    # Verbatim source: either-of {transcript_jsonl_path, transcript_md}.
    # jsonl_path wins on conflict (cursor canonical path; web wouldn't
    # legitimately pass both). See agent-bus thread 1026.
    if not body.transcript_jsonl_path and not body.transcript_md:
        _raise_422(
            reason="transcript_source.missing",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                "either transcript_jsonl_path (cursor) or transcript_md "
                "(web) is required — neither was supplied"
            ),
        )

    if body.transcript_jsonl_path:
        try:
            resolved_path = resolve_jsonl_path(body.transcript_jsonl_path)
        except TranscriptPathError as exc:
            _raise_422(
                reason="transcript_jsonl.invalid",
                session_id=body.session_id,
                agent=body.agent,
                detail=str(exc),
            )

        try:
            verbatim_md, turn_count = assemble_verbatim_md(
                jsonl_path=resolved_path,
                session_id=body.session_id,
                assistant_label=body.assistant_label,
            )
        except ValueError as exc:
            _raise_422(
                reason="transcript_jsonl.invalid",
                session_id=body.session_id,
                agent=body.agent,
                detail=f"JSONL parse error: {exc}",
            )
    else:
        # Web path: caller-supplied markdown is the verbatim layer as-is.
        # turn_count is best-effort from H2 ``## Turn`` headings; the
        # web preprocessor emits these per agent_skill:web-transcript-
        # preprocessing.md.
        assert body.transcript_md is not None
        verbatim_md = body.transcript_md
        turn_count = sum(
            1 for line in verbatim_md.splitlines() if line.startswith("## Turn")
        )

    transcript_md = compose_full_transcript(verbatim_md, body.session_summary_md)

    # Defense-in-depth: assembled markdown must still satisfy the on-disk
    # dual-layer doctrine.  These checks should ALWAYS pass when the JSONL
    # was non-trivial; failure indicates an empty/corrupt JSONL or a caller
    # somehow supplying an empty structural layer that slipped past earlier
    # guards.  Surface as transcript.hollow / transcript.missing_structure
    # so existing event consumers don't need new reasons.
    if len(transcript_md) < 200:
        _raise_422(
            reason="transcript.missing_structure",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                f"composed transcript is {len(transcript_md)} chars "
                "(< 200) — JSONL may be empty or session_summary_md too thin."
            ),
        )
    if "## Turn" not in transcript_md and "## Session Summary" not in transcript_md:
        _raise_422(
            reason="transcript.missing_structure",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                "composed transcript missing structural headings — assembly "
                "did not produce '## Turn' blocks and structural layer lacks "
                "'## Session Summary'."
            ),
        )
    if len(_USER_VOICE_RE.findall(transcript_md)) == 0:
        _raise_422(
            reason="transcript.hollow",
            session_id=body.session_id,
            agent=body.agent,
            detail=(
                "composed transcript has zero User-voice blocks. The "
                "supplied JSONL contained no user messages (or only "
                "tool_result records). Confirm transcript_jsonl_path "
                "points at the active session, not a continuation-with-"
                "no-prompt or a tool-only record set."
            ),
        )

    # ── derive values ──
    transcript_entity_id = f"transcript:{body.session_id}"
    transcript_path = f"notes/system/transcripts/{body.session_id}.md"
    source_uri = f"files://{transcript_path}"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_at = _parse_opened_at(body.session_id)

    # ── idempotency gate ──
    _idem_conn = cortex_conn()
    try:
        existing = _idem_conn.execute(
            "SELECT id, file_path FROM session_journals WHERE session_id = ?",
            (body.session_id,),
        ).fetchone()
    finally:
        _idem_conn.close()
    if existing is not None:
        already_detail = {
            "reason": "session.already_closed",
            "session_id": body.session_id,
            "transcript_entity_id": transcript_entity_id,
            "transcript_path": existing["file_path"] or transcript_path,
            "journal_row_id": existing["id"],
            "message": (
                f"session_close: {body.session_id} is already closed "
                f"(journal_row_id={existing['id']}). The previous close is "
                "the source of truth — do not retry; treat this as success."
            ),
        }
        _emit_rejected(
            "session.already_closed",
            session_id=body.session_id,
            agent=body.agent,
            detail=already_detail["message"],
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=already_detail,
        )

    # ── write transcript to disk under CORTEX_FILES_ROOT ──
    abs_path = _FILES_ROOT / transcript_path
    try:
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(transcript_md, encoding="utf-8")
    except OSError as exc:
        logger.error(
            "session_close: failed to write transcript to %s: %s", abs_path, exc
        )
        record(
            "mcp.session.close.write.failed",
            session_id=body.session_id,
            agent=body.agent,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Transcript file write failed: {exc}",
        )

    handoff_prompt = body.handoff_prompt.strip() if body.handoff_prompt else None

    name_words = body.summary.split()[:6]
    entity_name = " ".join(name_words)
    if len(name_words) < len(body.summary.split()):
        entity_name += "…"

    conn = cortex_conn()
    handoff_entry_id: int | None = None
    journal_row_id = 0
    audit_warnings: list[dict] | None = None
    try:
        conn.execute(
            "INSERT OR IGNORE INTO entities "
            "(id, type, name, description, status, source_uri, attributes, "
            "created_at, updated_at) "
            "VALUES (?, 'transcript', ?, ?, 'confirmed', ?, ?, ?, ?)",
            (
                transcript_entity_id,
                entity_name,
                body.summary,
                source_uri,
                json_encode(
                    {
                        "opened_at": opened_at,
                        "closed_at": now,
                        "status": "confirmed",
                    }
                ),
                now,
                now,
            ),
        )
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                transcript_path,
                body.session_id,
                body.prior_session_id,
            ),
        )
        journal_row_id = cur.lastrowid or 0

        if body.prior_session_id:
            _ensure_transcript_entity(conn, body.prior_session_id, body.agent, now)
            _ensure_continues_edge(
                conn, body.session_id, body.prior_session_id, body.agent, now
            )

        if handoff_prompt:
            handoff_entry_id = _insert_reflective_entry_tx(
                conn,
                agent=body.agent,
                register="self",
                entry=handoff_prompt,
                kind="handoff",
                session_id=body.session_id,
            )
            _insert_journal_link_tx(
                conn,
                from_entry=handoff_entry_id,
                to_entity=transcript_entity_id,
                link_type="handoff_for",
            )

        conn.commit()
        findings = _audit_normalization_refusals_for_session(
            conn, body.session_id
        )
        audit_warnings = findings if findings else None
    except Exception:
        conn.rollback()
        try:
            abs_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to unlink transcript after DB rollback: %s", abs_path
            )
            record(
                "mcp.session.close.cleanup.failed",
                session_id=body.session_id,
                agent=body.agent,
            )
        logger.error(
            "session_close DB transaction failed for %s",
            body.session_id,
            exc_info=True,
        )
        raise
    finally:
        conn.close()

    content_hash = compute_text_content_hash(transcript_md)
    logger.info(
        "session_close: %s agent=%s entity=%s journal_row=%d hash=%s",
        body.session_id,
        body.agent,
        transcript_entity_id,
        journal_row_id,
        content_hash,
    )
    record(
        "mcp.session.close.atomic",
        agent=body.agent,
        session_id=body.session_id,
        transcript_path=transcript_path,
        content_hash=content_hash,
        turn_count=turn_count,
        byte_count=len(transcript_md.encode("utf-8")),
    )

    return SessionCloseResponse(
        transcript_entity_id=transcript_entity_id,
        handoff_entry_id=handoff_entry_id,
        transcript_path=transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
        content_hash=content_hash,
        turn_count=turn_count,
        byte_count=len(transcript_md.encode("utf-8")),
        audit_warnings=audit_warnings,
    )


def _list_session_journals_impl(
    *, agent: str | None = None, limit: int = 3
) -> dict[str, object]:
    return list_session_journals(agent=agent, limit=limit).model_dump(mode="json")


def _create_session_journal_impl(payload: dict[str, object]) -> dict[str, object]:
    data = create_session_journal(SessionJournalCreate.model_validate(payload))
    return data.model_dump(mode="json")


def _close_session_impl(payload: dict[str, object]) -> dict[str, object]:
    data = close_session(SessionCloseRequest.model_validate(payload))
    return data.model_dump(mode="json")
