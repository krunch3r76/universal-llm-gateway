"""Session-journal and atomic session-close Pydantic models.

Session-close request shape: ``transcript_depth`` (default ``"verbatim"``)
selects the archival depth.  For ``verbatim`` / ``light``, the verbatim
source is **either-of** ``{transcript_jsonl_path, transcript_md}`` —
Cursor agents pass ``transcript_jsonl_path`` (a Cursor agent-transcripts
JSONL under ``CURSOR_AGENT_TRANSCRIPTS_ROOT``); the server reads it and
derives the verbatim layer.  Web agents (no JSONL on disk) pass
``transcript_md`` directly and the server uses it verbatim.  Either way
the agent-composed ``session_summary_md`` structural layer is appended.
If both are supplied, ``transcript_jsonl_path`` wins (cursor path is
canonical; web would not legitimately pass both).  For ``none``, no
transcript source is required and none is consumed.

Response carries ``content_hash`` (when a transcript file was written)
so the agent can quote it as provenance evidence per
`provenance-discipline.mdc` rule 1 (response-payload completion
contract) without re-reading the file.

See agent-bus thread 1026 for the either-of rationale (decision:
session-close-either-of-validator) and the
``session-close-transcript-depth-dial`` plan deck for the depth dial.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class _SessionJournalCommon(BaseModel):
    timestamp: str
    agent: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    entity_ids: list[str] | None = None
    file_path: str | None = None
    session_id: str | None = None
    prior_session_id: str | None = None
    handoff_prompt: str | None = None


class SessionJournalCreate(_SessionJournalCommon):
    markdown_content: str | None = None


class SessionJournalItem(_SessionJournalCommon):
    id: int
    transcript_entity_id: str | None = None


class SessionJournalList(BaseModel):
    items: list[SessionJournalItem]


# --- Session Close (atomic) ---


class SessionCloseRequest(BaseModel):
    """Session-close input contract.

    Atomic assembly is server-side: the handler reads the JSONL (or
    accepts inline ``transcript_md`` for the Web seat), builds the
    verbatim layer, appends ``session_summary_md``, writes the file,
    and commits the DB transaction.

    ``transcript_depth`` (default ``"verbatim"``) selects what gets
    archived:

      - ``verbatim`` — full dual-layer transcript (verbatim turns +
        structural layer); transcript entity created; enrichment-eligible.
        Transcript source (``transcript_jsonl_path`` or ``transcript_md``)
        is required. Current behavior; backward-compatible default.
      - ``light``    — structural-layer-only file written; transcript
        entity created with ``attributes.transcript_depth="light"``;
        NOT enrichment-eligible (no verbatim turns to walk). File content
        is ``session_summary_md``; no ``transcript_md`` / JSONL required.
      - ``none``     — no file, no transcript entity; journal row +
        ``continues`` edge only. NOT enrichment-eligible. Incompatible
        with ``handoff_prompt`` / ``handoff_source_path`` (422).

    Continuity (journal row, prior_session_id linkage, open_items) is
    written for all depths. Only the transcript archival layer varies.

    Required fields:
      session_id:         ``{agent}-YYYY-MM-DD-HHMM`` (UTC at session **start**;
        boot ``session_id`` or JSONL birth time for Cursor — not close time).
      agent:              agent slug (cursor / web / orion / …).
      session_summary_md: agent-composed structural layer.  Non-empty; a
        ``## Session Summary`` heading is normalized in (prepended or
        rewritten from a near-miss) when absent — see
        ``normalize_session_summary_heading``. A Decisions list is
        recommended but not enforced.
      summary:            ≥20 chars synthesis used for the entity name
        and journal row.

    Required conditionally on ``transcript_depth``:
      transcript_jsonl_path / transcript_md: required iff
        ``transcript_depth == "verbatim"``. Cursor passes the path;
        web passes the markdown. ``light`` uses ``session_summary_md`` only.

    Optional fields:
      transcript_depth:   one of {"verbatim", "light", "none"}; default
        ``"verbatim"``.
      domains / decisions / open_items / entity_ids: journal metadata.
      prior_session_id:   creates a ``continues`` edge.
      handoff_prompt:     forward-pickup narrative for the next session;
        persisted on the ``session_journals`` row in the
        ``handoff_prompt`` column (added in migration 044) and mirrored to
        ``transcript:{session_id}`` entity attributes for explicit retrieval
        via ``entity_get``. Boot omits handoffs (see
        ``decision:transcript-scoped-handoff-explicit-load``). Replaces the
        prior write path through ``reflective_journal`` (retired per
        ``decision:rj-handoff-kind-retirement``, agent-bus thread 1107).
      handoff_source_path / handoff_source_section: when ``handoff_source_path``
        is set, the server derives ``handoff_prompt`` from HTML-comment marker
        pairs in the file (2-A v2, agent-bus 1188). ``handoff_source_section``
        names an optional marker label; null uses ``<!-- handoff:start/end -->``.
      expected_handoff_prompt / expected_derived_handoff_prompt_sha256 /
        expected_source_file_sha256: assertion / TOCTOU guards after ``dry_run``.
      assistant_label:    H3 heading label for assistant blocks in the
        assembled verbatim layer (default ``"Assistant"``).
    """

    session_id: str
    agent: str
    transcript_jsonl_path: str | None = None
    transcript_md: str | None = None
    session_summary_md: str
    summary: str
    transcript_depth: Literal["verbatim", "light", "none"] = "verbatim"
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    entity_ids: list[str] | None = None
    prior_session_id: str | None = None
    handoff_prompt: str | None = None
    handoff_source_path: str | None = None
    handoff_source_section: str | None = None
    expected_handoff_prompt: str | None = None
    expected_derived_handoff_prompt_sha256: str | None = None
    expected_source_file_sha256: str | None = None
    assistant_label: str | None = None


class SessionCloseResponse(BaseModel):
    """Session-close response contract.

    ``content_hash`` is the SHA-256 of the composed transcript on disk
    (``sha256:<64 hex chars>``). Agents quote this in the user-facing
    completion line; per `provenance-discipline.mdc` rule 2, no
    read-back is required — the hash is the response-payload evidence
    that the close landed.

    For ``transcript_depth="none"``, ``transcript_entity_id``,
    ``transcript_path``, and ``content_hash`` are null (no file or
    transcript entity is created). ``turn_count`` is 0 for
    ``light`` / ``none``; ``byte_count`` is the structural-file size
    for ``light`` and 0 for ``none``.

    Agents reading the response use ``transcript_depth`` to gate
    enrichment per ``agent-skills/enrichment-quality-discipline.md``:
    only ``verbatim`` is enrichment-eligible.

    The handoff prompt (when supplied) is persisted on the session_journals
    row; journal_row_id is the durable handle for it.
    """

    transcript_entity_id: str | None = None
    transcript_path: str | None = None
    journal_row_id: int
    session_id: str
    transcript_depth: str
    content_hash: str | None = None
    turn_count: int
    byte_count: int
    # v1.3.1 Path 3 advisory (non-blocking): normalization refusals detected
    # in session-written assertions via the ledger. Never causes 422.
    audit_warnings: list[dict[str, Any]] | None = None
    debrief_turn_number: int | None = None
    debrief_status: (
        Literal["posted", "skipped_existing", "failed", "disabled"] | None
    ) = None
    debrief_body: str | None = None


class SessionHandoffUpsertRequest(BaseModel):
    """Post-close handoff upsert input.

    ``handoff_source_path`` (optional) is the cortex-relative path to the
    lead-authored ``.md`` handoff file this prompt was derived from. When
    supplied, the server stamps ``handoff_provenance`` (write path, ts,
    source file + sha256) on the transcript attribute so a reader can
    distinguish a file-backed handoff from a detached/bled-through string
    (agent-bus thread 1188; decision:handoff-surface-consistency).
    """

    handoff_prompt: str
    handoff_source_path: str | None = None
    handoff_source_section: str | None = None
    expected_handoff_prompt: str | None = None
    expected_derived_handoff_prompt_sha256: str | None = None
    expected_source_file_sha256: str | None = None


class SessionHandoffUpsertResponse(BaseModel):
    """Post-close handoff upsert result."""

    session_id: str
    handoff_prompt: str
    transcript_entity_id: str | None = None
    journal_row_id: int
