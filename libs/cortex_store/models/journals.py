"""Session-journal and atomic session-close Pydantic models.

Session-close request shape: **either-of** ``{transcript_jsonl_path,
transcript_md}`` is required.  Cursor agents pass
``transcript_jsonl_path`` (a Cursor agent-transcripts JSONL under
``CURSOR_AGENT_TRANSCRIPTS_ROOT``); the server reads it and derives the
verbatim layer.  Web agents (no JSONL on disk) pass ``transcript_md``
directly and the server uses it verbatim.  Either way the agent-composed
``session_summary_md`` structural layer is appended.  If both are
supplied, ``transcript_jsonl_path`` wins (cursor path is canonical;
web would not legitimately pass both).

Response carries ``content_hash`` so the agent can quote it as
provenance evidence per `provenance-discipline.mdc` rule 1 (response-
payload completion contract) without re-reading the file.

See agent-bus thread 1026 for the either-of rationale (decision:
session-close-either-of-validator).
"""

from __future__ import annotations

from typing import Any

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

    Atomic assembly is server-side: the handler reads the JSONL, builds
    the verbatim layer, appends `session_summary_md`, writes the file,
    and commits the DB transaction.  Agent-side cost is the path string
    (~150 bytes) plus the kilobyte-scale structural layer.

    Required fields:
      session_id:         ``{agent}-YYYY-MM-DD-HHMM`` (UTC wall clock).
      agent:              agent slug (cursor / web / orion / …).
      transcript_jsonl_path: absolute path under
        ``CURSOR_AGENT_TRANSCRIPTS_ROOT`` (default:
        ``~/.cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts``),
        or relative to that root.  Resolved + sandbox-checked by the
        handler.
      session_summary_md: agent-composed structural layer.  MUST start
        with a ``## Session Summary`` heading and include at minimum a
        Decisions list.  Concatenated to the assembled verbatim layer.
      summary:            ≥20 chars synthesis used for the entity name
        and journal row.

    Optional fields:
      domains / decisions / open_items / entity_ids: journal metadata
        arrays.
      prior_session_id:   creates a ``continues`` edge.
      handoff_prompt:     reflective journal handoff entry.
      assistant_label:    H3 heading label for assistant blocks in the
        assembled verbatim layer (default ``"Assistant"``).
    """

    session_id: str
    agent: str
    transcript_jsonl_path: str | None = None
    transcript_md: str | None = None
    session_summary_md: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    entity_ids: list[str] | None = None
    prior_session_id: str | None = None
    handoff_prompt: str | None = None
    assistant_label: str | None = None


class SessionCloseResponse(BaseModel):
    """Session-close response contract.

    ``content_hash`` is the SHA-256 of the composed transcript on disk
    (``sha256:<64 hex chars>``).  Agents quote this in the
    user-facing completion line; per `provenance-discipline.mdc` rule 2,
    no read-back is required — the hash is the response-payload evidence
    that the close landed.
    """

    transcript_entity_id: str
    handoff_entry_id: int | None = None
    transcript_path: str
    journal_row_id: int
    session_id: str
    content_hash: str
    turn_count: int
    byte_count: int
    # v1.3.1 Path 3 advisory (non-blocking): normalization refusals detected
    # in session-written assertions via the ledger. Never causes 422.
    audit_warnings: list[dict[str, Any]] | None = None
