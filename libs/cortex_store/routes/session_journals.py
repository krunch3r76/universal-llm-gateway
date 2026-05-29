from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, decode_row, json_encode, query
from ..dispatch_ops._shared import (
    _AGENT_SLUG_EXAMPLES,
    _AGENT_SLUG_RE,
    _AGENT_SLUG_RE_SOURCE,
    _FILES_ROOT,
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
    record,
)
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
    build_validation_error,
)
from ..transcript_assembly import (
    TranscriptPathError,
    assemble_verbatim_md,
    compose_full_transcript,
    compute_text_content_hash,
    resolve_jsonl_path,
)

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
            "entity_ids, file_path, session_id, prior_session_id, handoff_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                body.handoff_prompt,
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


def _raise_422(
    *,
    reason: str,
    session_id: str,
    agent: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    """Emit `mcp.session.close.rejected` and raise the matching 422 in one call.

    When ``payload`` is supplied it becomes the ``HTTPException.detail`` body
    so callers receive the full structured error (field/received/expected/
    examples/hint) per the cross-tool validation-error contract. The plain
    ``detail`` string is still used for the event payload (human-readable).
    """
    _emit_rejected(reason, session_id=session_id, agent=agent, detail=detail)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=payload if payload is not None else detail,
    )


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

    ``body.transcript_depth`` (default ``"verbatim"``) selects the
    archival layer:

      - ``verbatim``: steps 2–9 as documented (current behavior).
      - ``light``: file content is ``session_summary_md`` alone; no
        verbatim assembly. Transcript entity carries
        ``attributes.transcript_depth="light"``.
      - ``none``: no file, no transcript entity. Journal row written
        with ``file_path=NULL``; continues edge + handoff entry written
        per the universal continuity path (without ``handoff_for`` link).
        Response transcript_entity_id / transcript_path / content_hash
        are null.

    On any failure between steps 2 and 8 that occurs after the file is
    written, the file is unlinked before raising.
    On any failure after the transcript file is written, a best-effort
    ``Path.unlink`` is performed; an OSError on unlink is logged at WARNING
    and does not suppress the original exception.
    """
    if not _SESSION_ID_RE.match(body.session_id):
        payload = build_validation_error(
            reason="session_id.invalid",
            field="session_id",
            received=body.session_id,
            expected=_SESSION_ID_RE_SOURCE,
            examples=_SESSION_ID_EXAMPLES,
            hint=(
                "Agent slugs may contain hyphens (e.g. claude-web, "
                "api-claude) — the full slug must precede the "
                "YYYY-MM-DD-HHMM timestamp."
            ),
            detail=(
                f"session_id {body.session_id!r} does not match "
                f"pattern {_SESSION_ID_RE_SOURCE} "
                "({agent-slug}-YYYY-MM-DD-HHMM, lowercase)."
            ),
        )
        _raise_422(
            reason="session_id.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=payload["error"],
            payload=payload,
        )

    if not _AGENT_SLUG_RE.match(body.agent):
        payload = build_validation_error(
            reason="agent.invalid",
            field="agent",
            received=body.agent,
            expected=_AGENT_SLUG_RE_SOURCE,
            examples=list(_AGENT_SLUG_EXAMPLES),
            hint=(
                "agent is a routing/metadata hint (no allowlist) — must "
                "be a lowercase slug starting with a letter (hyphens "
                "allowed)."
            ),
            detail=(
                f"agent {body.agent!r} is not a valid lowercase slug "
                f"(expected {_AGENT_SLUG_RE_SOURCE})."
            ),
        )
        _raise_422(
            reason="agent.invalid",
            session_id=body.session_id,
            agent=body.agent,
            detail=payload["error"],
            payload=payload,
        )

    def _structured(
        reason: str,
        field: str,
        received: object,
        expected: str,
        examples: list[str],
        hint: str,
        detail: str,
    ) -> None:
        payload = build_validation_error(
            reason=reason,
            field=field,
            received=received,
            expected=expected,
            examples=examples,
            hint=hint,
            detail=detail,
        )
        _raise_422(
            reason=reason,
            session_id=body.session_id,
            agent=body.agent,
            detail=detail,
            payload=payload,
        )

    if len(body.summary) < 20:
        _structured(
            "summary.too_short",
            "summary",
            body.summary,
            "length >= 20",
            [],
            (
                "summary is the short synthesis used for the journal row + "
                "entity name — write at least one full sentence."
            ),
            f"summary must be >= 20 characters (got {len(body.summary)})",
        )

    if not body.session_summary_md.strip():
        _structured(
            "session_summary.invalid",
            "session_summary_md",
            body.session_summary_md,
            "non-empty markdown with '## Session Summary' heading",
            ["## Session Summary\\n…\\n## Decisions\\n…"],
            (
                "session_summary_md is the structural layer the agent "
                "composes — must start with a '## Session Summary' H2."
            ),
            "session_summary_md is required (structural layer).",
        )
    if "## Session Summary" not in body.session_summary_md:
        _structured(
            "session_summary.invalid",
            "session_summary_md",
            body.session_summary_md[:120]
            + ("…" if len(body.session_summary_md) > 120 else ""),
            "must contain heading '## Session Summary'",
            ["## Session Summary\\n…\\n## Decisions\\n…"],
            (
                "Add a '## Session Summary' H2 to the structural layer "
                "(decisions, files modified, continuation state)."
            ),
            (
                "session_summary_md must contain a '## Session Summary' "
                "heading; this is the structural layer the agent composes "
                "(decisions, files modified, continuation state)."
            ),
        )

    # Verbatim source: either-of {transcript_jsonl_path, transcript_md}.
    # jsonl_path wins on conflict (cursor canonical path; web wouldn't
    # legitimately pass both). See agent-bus thread 1026.
    # Required only when transcript_depth ∈ {verbatim, light}; for
    # transcript_depth=none, no transcript source is needed.
    if (
        body.transcript_depth != "none"
        and not body.transcript_jsonl_path
        and not body.transcript_md
    ):
        _structured(
            "transcript_source.missing",
            "transcript_jsonl_path|transcript_md",
            None,
            (
                f"exactly one of {{transcript_jsonl_path (cursor), "
                f"transcript_md (web)}} for transcript_depth="
                f"{body.transcript_depth!r}"
            ),
            [],
            (
                "Cursor agents pass transcript_jsonl_path under "
                "CURSOR_AGENT_TRANSCRIPTS_ROOT; web agents pass the "
                "verbatim markdown via transcript_md. For mechanical or "
                "bus-durable sessions where no transcript archival is "
                'needed, pass transcript_depth="none".'
            ),
            (
                f"either transcript_jsonl_path (cursor) or transcript_md "
                f"(web) is required for transcript_depth="
                f"{body.transcript_depth!r} — neither was supplied"
            ),
        )

    if body.transcript_depth == "none":
        # No file, no assembly, no hollow guards. transcript_md is unused.
        transcript_md = None
        turn_count = 0
    elif body.transcript_depth == "light":
        # File content is the structural layer only. No verbatim
        # assembly. Only the structural-headings hollow guard applies
        # here; the '## Session Summary' presence check was already
        # enforced upstream in _validate_session_close_args /
        # session_summary.invalid, but defensive duplication keeps the
        # atomic boundary self-sufficient.
        transcript_md = body.session_summary_md
        turn_count = 0
        if "## Session Summary" not in transcript_md:
            _structured(
                "transcript.missing_structure",
                "session_summary_md",
                None,
                "structural layer must contain '## Session Summary'",
                [],
                (
                    "For transcript_depth=light, the structural layer is "
                    "the entire transcript file — it must contain a "
                    "'## Session Summary' heading."
                ),
                "session_summary_md missing '## Session Summary' heading",
            )
    else:
        # verbatim (default)
        if body.transcript_jsonl_path:
            try:
                resolved_path = resolve_jsonl_path(body.transcript_jsonl_path)
            except TranscriptPathError as exc:
                _structured(
                    "transcript_jsonl.invalid",
                    "transcript_jsonl_path",
                    body.transcript_jsonl_path,
                    "absolute or relative path under CURSOR_AGENT_TRANSCRIPTS_ROOT",
                    [],
                    (
                        "Pass the active session's JSONL path under the cursor "
                        "agent-transcripts root; the server resolves + sandboxes it."
                    ),
                    str(exc),
                )

            try:
                verbatim_md, turn_count = assemble_verbatim_md(
                    jsonl_path=resolved_path,
                    session_id=body.session_id,
                    assistant_label=body.assistant_label,
                )
            except ValueError as exc:
                _structured(
                    "transcript_jsonl.invalid",
                    "transcript_jsonl_path",
                    body.transcript_jsonl_path,
                    "well-formed JSONL parseable by assemble_verbatim_md",
                    [],
                    (
                        "Confirm the JSONL is the cursor agent-transcripts "
                        "format (one record per line, user/assistant roles)."
                    ),
                    f"JSONL parse error: {exc}",
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
        # so existing event consumers don't need new reasons. Verbatim-only —
        # these checks are tuned for the dual-layer doctrine.
        if len(transcript_md) < 200:
            _structured(
                "transcript.missing_structure",
                "transcript_md|transcript_jsonl_path",
                len(transcript_md),
                "composed transcript length >= 200",
                [],
                (
                    "Either JSONL is empty or session_summary_md is too thin; "
                    "check the JSONL path and re-run."
                ),
                (
                    f"composed transcript is {len(transcript_md)} chars "
                    "(< 200) — JSONL may be empty or session_summary_md too thin."
                ),
            )
        if "## Turn" not in transcript_md and "## Session Summary" not in transcript_md:
            _structured(
                "transcript.missing_structure",
                "transcript_md|session_summary_md",
                None,
                "composed transcript contains '## Turn' or '## Session Summary'",
                [],
                (
                    "JSONL produced no turn blocks and structural layer lacks "
                    "'## Session Summary' — verify both sources."
                ),
                (
                    "composed transcript missing structural headings — assembly "
                    "did not produce '## Turn' blocks and structural layer lacks "
                    "'## Session Summary'."
                ),
            )
        if len(_USER_VOICE_RE.findall(transcript_md)) == 0:
            _structured(
                "transcript.hollow",
                "transcript_jsonl_path|transcript_md",
                None,
                "composed transcript contains >=1 User-voice block",
                [],
                (
                    "JSONL contained no user messages — likely pointing at a "
                    "continuation-with-no-prompt or tool-only record set."
                ),
                (
                    "composed transcript has zero User-voice blocks. The "
                    "supplied JSONL contained no user messages (or only "
                    "tool_result records). Confirm transcript_jsonl_path "
                    "points at the active session, not a continuation-with-"
                    "no-prompt or a tool-only record set."
                ),
            )

    # ── derive values ──
    transcript_entity_id: str | None = (
        None if body.transcript_depth == "none" else f"transcript:{body.session_id}"
    )
    transcript_path: str | None = (
        None
        if body.transcript_depth == "none"
        else f"notes/system/transcripts/{body.session_id}.md"
    )
    source_uri = f"files://{transcript_path}" if transcript_path else None
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    opened_at = _parse_opened_at(body.session_id)

    # ── idempotency gate (unchanged — UNIQUE(session_id) applies all depths) ──
    _idem_conn = cortex_conn()
    try:
        existing = _idem_conn.execute(
            "SELECT id, file_path FROM session_journals WHERE session_id = ?",
            (body.session_id,),
        ).fetchone()
    finally:
        _idem_conn.close()
    if existing is not None:
        # Echo the prior close's transcript_depth from the entity
        # attributes (or infer 'none' when no entity exists).
        prior_transcript_id = f"transcript:{body.session_id}"
        prior_depth = "none"
        with cortex_conn() as _depth_conn:
            depth_row = _depth_conn.execute(
                "SELECT attributes FROM entities WHERE id = ?",
                (prior_transcript_id,),
            ).fetchone()
            if depth_row and depth_row["attributes"]:
                try:
                    prior_attrs = json.loads(depth_row["attributes"])
                    prior_depth = prior_attrs.get("transcript_depth", "verbatim")
                except (json.JSONDecodeError, AttributeError):
                    prior_depth = "verbatim"
        already_detail = {
            "reason": "session.already_closed",
            "session_id": body.session_id,
            "transcript_entity_id": (
                prior_transcript_id if prior_depth != "none" else None
            ),
            "transcript_path": existing["file_path"],
            "journal_row_id": existing["id"],
            "transcript_depth": prior_depth,
            "message": (
                f"session_close: {body.session_id} is already closed "
                f"(journal_row_id={existing['id']}, depth={prior_depth}). "
                "The previous close is the source of truth — do not retry; "
                "treat this as success."
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

    # ── write transcript to disk under CORTEX_FILES_ROOT (skipped for depth=none) ──
    abs_path = None
    if transcript_path is not None:
        assert transcript_md is not None
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
    journal_row_id = 0
    audit_warnings: list[dict] | None = None
    try:
        # Transcript entity created only when depth ∈ {verbatim, light};
        # for depth=none, no entity is created (file_path is also NULL on
        # the journal row).
        if transcript_entity_id is not None:
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
                            "transcript_depth": body.transcript_depth,
                        }
                    ),
                    now,
                    now,
                ),
            )
        cur = conn.execute(
            "INSERT INTO session_journals "
            "(timestamp, agent, summary, domains, decisions, open_items, "
            "entity_ids, file_path, session_id, prior_session_id, handoff_prompt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                now,
                body.agent,
                body.summary,
                json_encode(body.domains),
                json_encode(body.decisions),
                json_encode(body.open_items),
                json_encode(body.entity_ids),
                transcript_path,  # None for depth=none
                body.session_id,
                body.prior_session_id,
                handoff_prompt,
            ),
        )
        journal_row_id = cur.lastrowid or 0

        if body.prior_session_id:
            # Ensure prior transcript entity exists (for back-links) and
            # write the continues edge. Safe for depth=none — session_edges
            # has no FK enforcement on from_node, so the dangling
            # transcript:{session_id} reference is harmless and preserves
            # continuity-graph traversal.
            _ensure_transcript_entity(conn, body.prior_session_id, body.agent, now)
            _ensure_continues_edge(
                conn, body.session_id, body.prior_session_id, body.agent, now
            )

        # handoff_prompt is persisted on the session_journals row above
        # (see the INSERT). No reflective_journal entry is written —
        # forward-pickup is session continuity, not reflection
        # (decision:rj-handoff-kind-retirement, agent-bus thread 1107).

        conn.commit()
        findings = _audit_normalization_refusals_for_session(conn, body.session_id)
        audit_warnings = findings if findings else None
    except Exception:
        conn.rollback()
        if abs_path is not None:
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

    content_hash: str | None = (
        compute_text_content_hash(transcript_md) if transcript_md is not None else None
    )
    byte_count = len(transcript_md.encode("utf-8")) if transcript_md is not None else 0
    logger.info(
        "session_close: %s agent=%s entity=%s journal_row=%d hash=%s depth=%s",
        body.session_id,
        body.agent,
        transcript_entity_id,
        journal_row_id,
        content_hash,
        body.transcript_depth,
    )
    record(
        "mcp.session.close.atomic",
        agent=body.agent,
        session_id=body.session_id,
        transcript_path=transcript_path,
        content_hash=content_hash,
        turn_count=turn_count,
        byte_count=byte_count,
        transcript_depth=body.transcript_depth,
    )

    return SessionCloseResponse(
        transcript_entity_id=transcript_entity_id,
        transcript_path=transcript_path,
        journal_row_id=journal_row_id,
        session_id=body.session_id,
        transcript_depth=body.transcript_depth,
        content_hash=content_hash,
        turn_count=turn_count,
        byte_count=byte_count,
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
