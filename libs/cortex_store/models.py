"""Pydantic schemas for Cortex API request/response payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Schema migration to structured types (list[str], etc.) was deferred as intentional
# debt. API contract is string-passthrough at the boundary; callers parse. Do not
# "fix" to strict types without a product decision — see thread 045.

AssertionConfidence = Literal["confirmed", "believed", "suspected", "hypothesized"]


def _reject_cortex_dropbox_source_uri(value: str | None) -> str | None:
    """Reject source_uri values that point into the cortex sandbox dropbox.

    dropbox/ in the cortex sandbox is a temporary, non-persistent staging area.
    Entities MUST reference permanent paths — the ingest flow is to read from
    dropbox, move to a permanent location, then record the permanent path.

    Accepts raw sandbox paths (``dropbox/...``) as well as ``files://`` URI
    forms (``files://dropbox/...``, ``files:///dropbox/...``). External URLs
    that merely contain the substring "dropbox" (e.g. ``https://dropbox.com/x``)
    are unaffected.
    """
    if value is None:
        return value
    normalized = value.removeprefix("files://").lstrip("/")
    first_segment = normalized.split("/", 1)[0]
    if first_segment == "dropbox":
        raise ValueError(
            "URI must not point into the cortex sandbox dropbox "
            "(temporary, non-persistent staging). Move the file to a "
            "permanent path and record that path instead. "
            f"Rejected: {value!r}"
        )
    return value


def _reject_cortex_dropbox_uri_list(value: list[str] | None) -> list[str] | None:
    """Apply dropbox rejection to each element of a URI list field."""
    if value is None:
        return value
    for uri in value:
        _reject_cortex_dropbox_source_uri(uri)
    return value


# --- Entities ---


class _EntityCommon(BaseModel):
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    content_hash: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


EntityStatus = Literal["confirmed", "provisional", "merged", "deprecated", "reaped"]
RetentionPolicy = Literal["permanent", "ephemeral", "archival"]


class EntityCreate(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None


class EntitySummary(BaseModel):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    content_hash: str | None = None
    created_at: str


class EntityDetail(_EntityCommon):
    id: str
    type: str
    name: str
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    created_at: str
    updated_at: str
    assertions: list[AssertionItem] = Field(default_factory=list)
    relationships: list[RelationshipItem] = Field(default_factory=list)
    reasoning_edges: list[EdgeItem] = Field(default_factory=list)
    action_hints: list[ActionHint] | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    attributes: dict[str, Any] | None = None
    notes: str | None = None
    source_uri: str | None = None
    description: str | None = None
    status: EntityStatus | None = None
    workflow_state: str | None = None
    content_hash: str | None = None
    retention_policy: RetentionPolicy | None = None
    retention_ttl_days: int | None = None

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


class EntityList(BaseModel):
    items: list[EntitySummary]


# --- Assertions ---


DerivationType = Literal[
    "quotation",
    "compression",
    "inference",
    "direct_observation",
    "agent_observation",
    "user_statement",
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
    # C2: explicit revision bypass — force=True skips contradiction check,
    # supersedes_id marks the target for atomic supersession
    force: bool = Field(
        False,
        description="Bypass C2 contradiction check",
    )
    supersedes_id: int | None = Field(
        None,
        description="Assertion to supersede when force=True",
    )

    _validate_artifact_uri = field_validator("artifact_uri")(
        _reject_cortex_dropbox_source_uri
    )
    _validate_evidence_uris = field_validator("evidence_uris")(
        _reject_cortex_dropbox_uri_list
    )


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

    _validate_evidence_uris = field_validator("evidence_uris")(
        _reject_cortex_dropbox_uri_list
    )


class SupersedeResponse(BaseModel):
    old: AssertionItem
    new: AssertionItem
    impact_warning: str | None = None


class NearDuplicateWarning(BaseModel):
    existing_id: int
    score: float


class ContradictionConflict(BaseModel):
    """A conflicting assertion detected by C2 write-path contradiction check."""

    assertion_id: int
    claim: str
    confidence: str
    similarity: float


class ActionHint(BaseModel):
    """Structured hint for data that likely needs agent action.

    Attached to read responses when temporal staleness or unresolved state
    is detected. Nudges agents to close the loop on stale data.
    """

    category: str
    target_id: int | None = None
    entity_id: str | None = None
    message: str
    action: str


class AssertionCreateResponse(BaseModel):
    was_new: bool
    item: AssertionItem
    near_duplicate_warning: NearDuplicateWarning | None = None
    validation_warnings: list[dict[str, str]] | None = None
    contradiction_warnings: list[ContradictionConflict] | None = None


class AssertionList(BaseModel):
    items: list[AssertionItem]
    action_hints: list[ActionHint] | None = None


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
    action_hints: list[ActionHint] | None = None


class TouchedAssertionItem(BaseModel):
    """Assertion touched by a proposed claim in C1 impact analysis."""

    assertion_id: int
    claim: str
    confidence: str
    similarity: float
    entity_id: str
    retrieval_source: str


class ImpactAnalysisRequest(BaseModel):
    entity_id: str
    claim: str
    confidence: AssertionConfidence = "believed"


class ImpactAnalysisResponse(BaseModel):
    touched_assertions: list[TouchedAssertionItem]
    likely_supersedes: list[int]
    implicated_entities: list[str]
    impact_score: float


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
    session_id: str | None = None
    agent: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


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
    session_id: str | None = None
    agent: str | None = None
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
    deadline_id: str | None = None
    deadline_name: str
    deadline_date: str | None = None
    deadline_description: str | None = None
    urgency: str | None = None
    outcome: str | None = None


class DeadlineList(BaseModel):
    items: list[DeadlineItem]
    action_hints: list[ActionHint] | None = None


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


class SessionCloseResponse(BaseModel):
    transcript_entity_id: str
    transcript_path: str
    journal_row_id: int
    session_id: str


# --- Chunks ---


class ChunkCreate(BaseModel):
    content: str
    source_uri: str
    source_date: str | None = None
    observer: str = "web-claude"
    chunk_index: int = 0
    extraction_run: int | None = None
    token_count: int | None = None

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


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

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


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

    _validate_evidence_uris = field_validator("evidence_uris")(
        _reject_cortex_dropbox_uri_list
    )


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

    _validate_source_uri = field_validator("source_uri")(
        _reject_cortex_dropbox_source_uri
    )


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


# --- Reflective Journal ---

ReflectiveKind = Literal["entry", "reflection", "revision", "consolidation"]
JournalLinkType = Literal[
    "contradicts",
    "refines",
    "supersedes",
    "reopens",
    "unresolved_with",
    "continues",
    "related",
]


class JournalLinkCreate(BaseModel):
    to_entry: int | None = None
    to_entity: str | None = None
    link_type: JournalLinkType


class JournalLinkItem(BaseModel):
    id: int
    from_entry: int
    to_entry: int | None = None
    to_entity: str | None = None
    link_type: JournalLinkType
    created_at: str


class ConsolidationData(BaseModel):
    """Structured consolidation synthesis with anti-coherence-theater safeguards."""

    throughline: str
    before: str
    now: str
    tension_points: list[str] = Field(default_factory=list)
    contradiction_set: list[str] = Field(default_factory=list)
    falsifier: str | None = None
    rendered_shift: str | None = None
    confidence: str | None = None
    source_entry_ids: list[int] = Field(default_factory=list)


class ReflectiveEntryCreate(BaseModel):
    agent: str
    register: str
    entry: str
    kind: ReflectiveKind = "entry"
    session_id: str | None = None
    revises: int | None = None
    links: list[JournalLinkCreate] | None = None
    consolidation_data: ConsolidationData | None = None


class ReflectiveEntryItem(BaseModel):
    id: int
    agent: str
    register: str
    entry: str
    kind: ReflectiveKind
    session_id: str | None = None
    revises: int | None = None
    consolidation_data: dict[str, Any] | None = None
    links: list[JournalLinkItem] = Field(default_factory=list)
    suggested_links: list[dict[str, Any]] | None = None
    created_at: str


class ReflectiveEntryList(BaseModel):
    items: list[ReflectiveEntryItem]
    total: int
