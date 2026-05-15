"""Session-journal and atomic session-close Pydantic models."""

from __future__ import annotations

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
    # Optional session identity — used to auto-create transcript entity and
    # write continues edges server-side.
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
    session_id: str
    agent: str
    transcript_md: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    entity_ids: list[str] | None = None
    prior_session_id: str | None = None
    handoff_prompt: str | None = None


class SessionCloseResponse(BaseModel):
    transcript_entity_id: str
    handoff_entry_id: int | None = None
    transcript_path: str
    journal_row_id: int
    session_id: str
