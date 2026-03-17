from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# --- Todos ---


class TodoCreate(BaseModel):
    id: str
    title: str
    domain: str
    context: str = "universal-llm-gateway"
    priority: str = "short_term"
    description: str = ""
    refs: dict[str, str] = Field(default_factory=dict)


class TodoStatusUpdate(BaseModel):
    status: str = Field(description="One of: done, deferred, open")


class TodoItem(BaseModel):
    id: str
    title: str
    status: str
    priority: str
    domain: str
    context: str
    description: str = ""
    refs: dict[str, str] = Field(default_factory=dict)
    notes: str = ""
    created_at: str | None = None
    updated_at: str | None = None


class TodoList(BaseModel):
    items: list[TodoItem]


# --- Entities ---


class EntityCreate(BaseModel):
    id: str
    type: str
    name: str
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    created_at: str


class EntityDetail(BaseModel):
    id: str
    type: str
    name: str
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- Assertions ---


class AssertionCreate(BaseModel):
    entity_id: str
    claim: str
    confidence: str = Field(
        description="One of: confirmed, believed, suspected, hypothesized"
    )
    evidence: str
    evidence_uris: list[str] | None = None


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: str
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


class SessionJournalCreate(BaseModel):
    timestamp: str
    agent: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    file_path: str | None = None


class SessionJournalItem(BaseModel):
    id: int
    timestamp: str
    agent: str
    summary: str
    domains: list[str] | None = None
    decisions: list[str] | None = None
    open_items: list[str] | None = None
    file_path: str | None = None


class SessionJournalList(BaseModel):
    items: list[SessionJournalItem]


EntityDetail.model_rebuild()
