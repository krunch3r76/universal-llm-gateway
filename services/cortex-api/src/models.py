"""Pydantic schemas for Cortex API request/response payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Schema migration to structured types (list[str], etc.) was deferred as intentional
# debt. API contract is string-passthrough at the boundary; callers parse. Do not
# "fix" to strict types without a product decision — see thread 045.

# --- Todos ---

TodoStatus = Literal["done", "deferred", "open"]
AssertionConfidence = Literal["confirmed", "believed", "suspected", "hypothesized"]


class TodoCreate(BaseModel):
    id: str
    title: str
    domain: str
    context: str = "universal-llm-gateway"
    priority: str = "short_term"
    description: str = ""
    refs: dict[str, Any] = Field(default_factory=dict)


class TodoStatusUpdate(BaseModel):
    status: TodoStatus = Field(
        description="One of: done, deferred, open"
    )


class TodoItem(BaseModel):
    id: str
    title: str
    status: TodoStatus
    priority: str
    domain: str
    context: str
    description: str = ""
    refs: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class TodoList(BaseModel):
    items: list[TodoItem]


# --- Entities ---


class _EntityCommon(BaseModel):
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None


class EntityCreate(_EntityCommon):
    id: str
    type: str
    name: str


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    created_at: str


class EntityDetail(_EntityCommon):
    id: str
    type: str
    name: str
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- Assertions ---


class AssertionCreate(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = Field(
        description="One of: confirmed, believed, suspected, hypothesized"
    )
    evidence: str
    evidence_uris: list[str] | None = None


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    created_at: str


class AssertionList(BaseModel):
    items: list[AssertionItem]


# --- Deadlines ---


class DeadlineItem(BaseModel):
    matter_id: str
    matter_name: str
    deadline_name: str
    deadline_date: str | None = None
    deadline_description: str | None = None


class DeadlineList(BaseModel):
    items: list[DeadlineItem]


# --- Session Journals ---


class _SessionJournalCommon(BaseModel):
    timestamp: str
    agent: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    file_path: str | None = None


class SessionJournalCreate(_SessionJournalCommon):
    pass


class SessionJournalItem(_SessionJournalCommon):
    id: int


class SessionJournalList(BaseModel):
    items: list[SessionJournalItem]


EntityDetail.model_rebuild()
