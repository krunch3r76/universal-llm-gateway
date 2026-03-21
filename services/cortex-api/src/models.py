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
    status: TodoStatus = Field(description="One of: done, deferred, open")


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


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated"]


class EntityCreate(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    created_at: str


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated"]


class EntityDetail(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated"]


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated"]


class EntityUpdate(BaseModel):
    notes: str | None = None
    description: str | None = None
    status: EntityStatus | None = None


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- Assertions ---


DerivationType = Literal["quotation", "compression", "inference", "other"]
ValidityPrecision = Literal["exact", "approximate", "inferred"]
TemporalType = Literal["event", "state", "unknown"]


class AssertionCreate(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = Field(
        description="One of: confirmed, believed, suspected, hypothesized"
    )
    evidence: str
    evidence_uris: list[str] | None = None
    chunk_id: int | None = None
    derivation_type: DerivationType | None = None
    reasoning_summary: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    validity_precision: ValidityPrecision | None = None
    confidence_score: float | None = None
    temporal_type: TemporalType | None = None
    is_atomic: bool = True
    is_decontextualized: bool = True


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    chunk_id: int | None = None
    derivation_type: DerivationType | None = None
    reasoning_summary: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    validity_precision: ValidityPrecision | None = None
    confidence_score: float | None = None
    temporal_type: TemporalType | None = None
    is_atomic: bool | None = None
    is_decontextualized: bool | None = None
    human_reviewed: bool | None = None
    superseded_by: int | None = None
    review_notes: str | None = None
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


# --- Chunks ---


class ChunkCreate(BaseModel):
    content: str
    source_uri: str | None = None
    source_hash: str | None = None
    source_date: str | None = None
    chunk_index: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    observer: str = "cursor"
    model_version: str | None = None


class ChunkItem(BaseModel):
    id: int
    content: str
    source_uri: str | None = None
    source_hash: str | None = None
    source_date: str | None = None
    chunk_index: int | None = None
    offset_start: int | None = None
    offset_end: int | None = None
    observer: str | None = None
    model_version: str | None = None
    created_at: str


class ChunkList(BaseModel):
    items: list[ChunkItem]


# --- Surface Forms ---


class SurfaceFormCreate(BaseModel):
    entity_id: str
    form: str
    chunk_id: int
    mention: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    context_hash: str | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    entity_type_hint: str | None = None


class SurfaceFormItem(BaseModel):
    id: int
    entity_id: str
    form: str
    chunk_id: int
    mention: str | None = None
    span_start: int | None = None
    span_end: int | None = None
    context_hash: str | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    entity_type_hint: str | None = None
    created_at: str


class SurfaceFormList(BaseModel):
    items: list[SurfaceFormItem]


class SurfaceFormCacheResult(BaseModel):
    hit: bool
    entity_id: str | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None


# --- Staging ---

StagingStatus = Literal["pending", "approved", "rejected", "merged"]
ProposalType = Literal["entity", "assertion"]
ProposalAction = Literal["add", "revise", "remove"]


class StagingProposalCreate(BaseModel):
    source_uri: str | None = None
    proposal_type: ProposalType
    proposal_action: ProposalAction = "add"
    target_id: str | None = None
    proposal_json: dict[str, Any]
    chunk_id: int | None = None


class StagingBatchCreate(BaseModel):
    proposals: list[StagingProposalCreate]


class StagingItem(BaseModel):
    id: int
    source_uri: str | None = None
    proposal_type: ProposalType
    proposal_action: ProposalAction
    target_id: str | None = None
    proposal_json: dict[str, Any] | None = None
    chunk_id: int | None = None
    status: StagingStatus
    resolved_to: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    created_at: str


class StagingList(BaseModel):
    items: list[StagingItem]


class StagingApproval(BaseModel):
    reviewer: str = "human"
