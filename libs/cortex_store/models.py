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


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated", "reaped"]
RetentionPolicy = Literal["permanent", "ephemeral", "archival"]


class EntityCreate(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None


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
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None


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
    "commitment",
    "other",
]

ResolutionStatus = Literal["pending", "fulfilled", "breached", "unknown"]


class AssertionCreate(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = Field(
        description="One of: confirmed, believed, suspected, hypothesized"
    )
    evidence: str
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
    chunk_id: int | None = None
    derivation_type: DerivationType | None = None
    reasoning_summary: str | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    confidence_score: float | None = None
    is_atomic: bool = True
    is_decontextualized: bool = True
    resolution_status: ResolutionStatus | None = None
    fulfillment_assertion_id: int | None = None
    # v3: Kumiho grounding — BYO-storage + consolidation enrichment
    prospective_summary: str | None = None
    events_json: str | None = None
    artifact_uri: str | None = None
    artifact_storage: str = "inline"


ReviewStatus = Literal["committed", "flagged", "staged", "rejected"]


class AssertionItem(BaseModel):
    id: int
    entity_id: str | None = None
    claim: str
    confidence: AssertionConfidence
    confidence_score: float | None = None
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
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
    resolution_status: str | None = None
    fulfillment_assertion_id: int | None = None
    quality_score: float | None = None
    # v3: Kumiho grounding — BYO-storage + consolidation enrichment
    prospective_summary: str | None = None
    events_json: str | None = None
    artifact_uri: str | None = None
    artifact_storage: str | None = None
    entrenchment_score: float | None = None
    created_at: str


class AssertionUpdate(BaseModel):
    superseded_by: int | None = None
    valid_until: str | None = None
    confidence: AssertionConfidence | None = None
    confidence_score: float | None = None
    review_status: ReviewStatus | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    resolution_status: ResolutionStatus | None = None
    fulfillment_assertion_id: int | None = None


class SupersedeRequest(BaseModel):
    old_assertion_id: int
    entity_id: str
    claim: str
    confidence: AssertionConfidence
    evidence: str
    evidence_uris: list[str] | None = None
    valid_from: str | None = None
    derivation_type: DerivationType | None = None
    session_id: str
    agent: str


class SupersedeResponse(BaseModel):
    old: AssertionItem
    new: AssertionItem


class NearDuplicateWarning(BaseModel):
    existing_id: int
    score: float


class AssertionCreateResponse(BaseModel):
    was_new: bool
    item: AssertionItem
    near_duplicate_warning: NearDuplicateWarning | None = None
    validation_warnings: list[dict[str, str]] | None = None


class AssertionList(BaseModel):
    items: list[AssertionItem]


class AssertionSearchItem(BaseModel):
    """Assertion search result with hybrid retrieval scores."""

    id: int
    entity_id: str | None = None
    entity_name: str | None = None
    claim: str
    confidence: AssertionConfidence
    confidence_score: float | None = None
    evidence: str | None = None
    evidence_uris: list[str] | None = None
    seeded_by: str | None = None
    derivation_type: DerivationType | None = None
    prospective_summary: str | None = None
    events_json: str | None = None
    superseded_by: int | None = None
    entrenchment_score: float | None = None
    observed_at: str | None = None
    bm25_score: float | None = Field(None, description="Normalized BM25 score (0-1)")
    cosine_similarity: float | None = Field(
        None, description="Cosine similarity from vector search (0-1)"
    )
    combmax_score: float | None = Field(
        None, description="CombMAX fused score: max(bm25_norm, cosine_sim)"
    )
    retrieval_source: str = Field(
        "fts", description="Retrieval branch: 'fts', 'vector', or 'both'"
    )
    rank: float | None = Field(
        None, description="Raw FTS5 BM25 rank (lower = better match)"
    )
    created_at: str


class AssertionSearchResult(BaseModel):
    query: str
    items: list[AssertionSearchItem]
    total: int
    search_mode: str = Field(
        "hybrid", description="'hybrid' when vector is available, 'fulltext' otherwise"
    )


class EnrichRequest(BaseModel):
    enrichments: list[str] | None = Field(
        None,
        description='Enrichment kinds to run: "prospective", "events". '
        "Defaults to all if omitted.",
    )


class EnrichResponse(BaseModel):
    item: AssertionItem
    enrichments_run: list[str]
    results: dict[str, str | None]


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


class RelationshipCreateResponse(BaseModel):
    was_new: bool
    item: RelationshipItem


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
    entity_ids: list[str] | None = None
    file_path: str | None = None
    # Optional session identity — used to auto-create transcript entity and
    # write continues edges server-side.
    session_id: str | None = None
    prior_session_id: str | None = None


class SessionJournalCreate(_SessionJournalCommon):
    pass


class SessionJournalItem(_SessionJournalCommon):
    id: int
    transcript_entity_id: str | None = None


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


# --- Extraction Runs ---


# --- Ingest ---


class IngestDocumentRequest(BaseModel):
    source_uri: str
    content: str
    observer: str = "web"
    source_date: str | None = None


class ChunkResult(BaseModel):
    chunk_id: int
    chunk_index: int
    snippet: str = Field(description="First 200 chars of chunk content")
    extracted_dates: list[str]
    token_count: int


class IngestDocumentResponse(BaseModel):
    source_uri: str
    chunk_count: int
    chunks: list[ChunkResult]


class AssertFromChunkRequest(BaseModel):
    chunk_id: int
    entity_id: str
    claim: str
    confidence: str
    evidence: str
    evidence_uris: list[str] | None = None
    derivation_type: str | None = None
    confidence_score: float | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    reasoning_summary: str | None = None
    resolution_status: str | None = None
    seeded_by: str | None = None


class AssertFromChunkResponse(BaseModel):
    item: AssertionItem
    was_new: bool
    suggested_valid_from: str | None = None
    quality_score: float
    validation_warnings: list[dict[str, str]] | None = None


# --- Extraction Runs ---


class ExtractionCheckRequest(BaseModel):
    source_uri: str
    content_hash: str


class ExtractionCheckResponse(BaseModel):
    action: Literal["proceed", "skip", "re-extract"]
    run_id: int
    superseded_run_id: int | None = None
    superseded_assertion_count: int | None = None


class ExtractionRunComplete(BaseModel):
    status: Literal["completed", "failed"] = "completed"
    assertion_count: int = 0


class ExtractionRunItem(BaseModel):
    id: int
    source_uri: str
    content_hash: str | None = None
    status: str
    assertion_count: int | None = None
    created_at: str
    completed_at: str | None = None


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


# --- Session edges ---


class EdgeCreate(BaseModel):
    session_id: str
    agent: str
    from_node: str
    to_node: str
    edge_type: str
    strength: float = 0.8
    edge_source: str = "explicit"
    context: str | None = None
    prompt: str | None = None
    seeded_by: str | None = None
    metadata: str | None = None


class EdgeItem(BaseModel):
    id: int
    session_id: str
    agent: str
    from_node: str
    to_node: str
    edge_type: str
    strength: float
    edge_source: str
    context: str | None
    prompt: str | None
    seeded_by: str | None
    valid_until: str | None
    metadata: str | None
    created_at: str


class EdgeList(BaseModel):
    items: list[EdgeItem]
    count: int


class EdgeRetire(BaseModel):
    valid_until: str | None = None  # None = now()
