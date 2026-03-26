"""Pydantic schemas for Cortex API request/response payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Schema migration to structured types (list[str], etc.) was deferred as intentional
# debt. API contract is string-passthrough at the boundary; callers parse. Do not
# "fix" to strict types without a product decision — see thread 045.

AssertionConfidence = Literal["confirmed", "believed", "suspected", "hypothesized"]


# --- Entities ---


class _EntityCommon(BaseModel):
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    content_hash: str | None = None


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
    content_hash: str | None = None
    created_at: str


class EntityDetail(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)
    relationships: list[RelationshipItem] = Field(default_factory=list)


class EntityUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    description: str | None = None
    status: EntityStatus | None = None
    content_hash: str | None = None


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- Assertions ---


DerivationType = Literal[
    "quotation",
    "compression",
    "inference",
    "direct_observation",
    "agent_observation",
    "stated",
    "other",
]


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
    confidence_score: float | None = None
    is_atomic: bool = True
    is_decontextualized: bool = True


ReviewStatus = Literal["committed", "flagged", "staged", "rejected"]


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    confidence_score: float | None = None
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    derivation_type: DerivationType | None = None
    chunk_id: int | None = None
    reasoning_summary: str | None = None
    is_atomic: bool | None = None
    is_decontextualized: bool | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: int | None = None
    review_status: ReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    created_at: str


class AssertionUpdate(BaseModel):
    superseded_by: int | None = None
    valid_until: str | None = None
    confidence: AssertionConfidence | None = None
    confidence_score: float | None = None
    review_status: ReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None


class SupersedeRequest(BaseModel):
    old_assertion_id: int
    entity_id: str
    claim: str
    confidence: AssertionConfidence
    evidence: str
    evidence_uris: list[str] | None = None
    valid_from: str | None = None
    derivation_type: DerivationType | None = None


class SupersedeResponse(BaseModel):
    old: AssertionItem
    new: AssertionItem


class AssertionList(BaseModel):
    items: list[AssertionItem]


# --- Relationships ---


class RelationshipCreate(BaseModel):
    source_id: str
    target_id: str
    type_id: str
    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    chunk_id: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None


class RelationshipItem(BaseModel):
    id: int
    source_id: str
    target_id: str
    type_id: str
    type_name: str | None = None
    source_name: str | None = None
    target_name: str | None = None
    role: str | None = None
    strength: float | None = None
    evidence: str | None = None
    chunk_id: int | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    source_uri: str | None = None
    created_at: str


class RelationshipList(BaseModel):
    items: list[RelationshipItem]


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
    source_uri: str
    source_date: str | None = None
    observer: str = "web-claude"
    chunk_index: int = 0
    extraction_run: int | None = None
    token_count: int | None = None


class ChunkItem(BaseModel):
    id: int
    content: str
    source_uri: str
    source_date: str | None = None
    observer: str | None = None
    chunk_index: int | None = None
    extraction_run: int | None = None
    token_count: int | None = None
    created_at: str


class ChunkList(BaseModel):
    items: list[ChunkItem]


# --- Surface Forms ---


class SurfaceFormCreate(BaseModel):
    mention: str
    entity_id: str
    chunk_id: int | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    context_hash: str | None = None
    mention_type: str | None = None


class SurfaceFormItem(BaseModel):
    id: int
    mention: str
    entity_id: str
    chunk_id: int | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
    context_hash: str | None = None
    mention_type: str | None = None
    created_at: str


class SurfaceFormList(BaseModel):
    items: list[SurfaceFormItem]


class SurfaceFormCacheResult(BaseModel):
    hit: bool
    entity_id: str | None = None
    resolution_confidence: float | None = None
    resolution_reasoning: str | None = None
