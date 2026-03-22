from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# Decay constant for recency scoring; half-life ≈ 69 days.
RECENCY_DECAY_LAMBDA = 0.01


class SearchRequest(BaseModel):
    """Request body for RAG /search. Scope and source_prefixes are mutually exclusive."""

    query: str
    top_k: int = 5
    recency_weight: float = 0.0
    max_distance: float | None = None  # None = return all (backward compat)
    source_prefixes: list[str] | None = None
    scope: str | list[str] | None = None
    # ∀ sparse_only=True: skip dense embedding + collection.query(); BM25 sidecar only.
    # Use for OR-joined named-entity queries where dense embedding adds noise.
    sparse_only: bool = False

    @model_validator(mode="after")
    def check_scope_source_prefixes_exclusive(self) -> SearchRequest:
        """Enforce mutual exclusivity of scope and source_prefixes."""
        if self.scope is not None and self.source_prefixes is not None:
            raise ValueError(
                "scope and source_prefixes are mutually exclusive; set only one"
            )
        return self


class SearchResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, Any]]
    distances: list[float]
    property_hits: int = 0


class IndexRequest(BaseModel):
    path: str
    metadata_overrides: dict[str, str | int | float | bool] | None = None
    force: bool = False


class IndexResult(BaseModel):
    indexed: int
    deleted: int
    unchanged: bool
    file: str
    duplicate: bool = False
    duplicate_of: str | None = None
    extraction_entities: int = 0
    extraction_topics: int = 0


class DeleteResult(BaseModel):
    file: str
    deleted: int


class IndexDirectoryRequest(BaseModel):
    path: str
    extensions: list[str] | None = None
    metadata_overrides: dict[str, str | int | float | bool] | None = None
    force: bool = False


class IndexDirectoryResponse(BaseModel):
    indexed: int
    deleted: int
    unchanged: int
    files: int
    duplicates: int = 0


class StatsResponse(BaseModel):
    count: int
    collection: str


class WatcherStatusItem(BaseModel):
    """Single watcher state row from WatcherManager status output."""

    path: str
    enabled: bool
    reload_count: int
    error_count: int


class IndexingStatusResponse(BaseModel):
    """Unified indexing health payload for operator-facing status clients."""

    pending_count: int
    pending_sample: list[str] = Field(default_factory=list)
    pending_sample_truncated: bool = False
    chunks: int | None = None
    collection: str | None = None
    chroma_available: bool = True
    chroma_error: str | None = None
    watchers: list[WatcherStatusItem] = Field(default_factory=list)
    failed_extractions_count: int = 0
    failed_extractions_permanent_count: int = 0
    property_index_available: bool = True


class ClearResponse(BaseModel):
    deleted: int
    collection: str


class SourceResponse(BaseModel):
    chunks: list[str]
    metadata: list[dict[str, str | int | float | bool]]


class SourcesResponse(BaseModel):
    """List of source file paths (e.g. for extraction export by prefix)."""

    sources: list[str]


class ScopeInfo(BaseModel):
    prefixes: list[str]
    description: str


class ScopesResponse(BaseModel):
    scopes: dict[str, ScopeInfo]


class ExtractionExportItem(BaseModel):
    """Single chunk with its extraction metadata, for bulk export."""

    source: str
    chunk_id: str
    chunk_index: int
    text: str
    extraction: str | None = None
    extraction_model: str | None = None
    extraction_schema_version: str | None = None


class ExtractionExportResponse(BaseModel):
    """Bulk extraction export: all chunks under a source prefix."""

    total_chunks: int
    total_sources: int
    items: list[ExtractionExportItem]


class SourceDeleteResponse(BaseModel):
    """Result of deleting a single source from all storage surfaces."""

    source: str
    chunks_deleted: int
    fts_removed: int
    properties_removed: int
    article_deleted: bool


class DirectoryDeleteResponse(BaseModel):
    """Result of deleting all sources under a directory prefix."""

    path: str
    sources_deleted: int
    chunks_deleted: int
    fts_removed: int
    articles_deleted: int


class ClearDirectoryRequest(BaseModel):
    path: str


class ClearDirectoryResponse(BaseModel):
    sources_cleared: int
    chunks_cleared: int


class ChunkIndexGroup(BaseModel):
    """A source file and the chunk indices to fetch from it."""

    source: str
    chunk_indices: list[int]


class ChunkByIndexItem(BaseModel):
    """A single chunk returned by the chunks_by_index endpoint."""

    chunk_id: str
    source: str
    chunk_index: int
    text: str
    metadata: dict[str, Any]


class ChunksByIndexRequest(BaseModel):
    """Batched request to fetch chunks by source + index position."""

    groups: list[ChunkIndexGroup]


class ChunksByIndexResponse(BaseModel):
    """Response from chunks_by_index endpoint."""

    chunks: list[ChunkByIndexItem]


class FailedChunkItem(BaseModel):
    chunk_id: str
    source: str
    error: str
    attempt_count: int
    recorded_at: str


class FailedExtractionResponse(BaseModel):
    total: int
    chunks: list[FailedChunkItem]


class ArticleUpsertRequest(BaseModel):
    """Upsert a row in the articles table. Only source_path is required;
    all other fields merge with any existing row (empty strings are ignored)."""

    source_path: str
    filename: str | None = None
    title: str = ""
    authors: str = ""
    venue: str = ""
    published_date: str = ""
    doi: str = ""
    abstract: str = ""
    content_hash: str = ""
    subdirectory: str = ""
    scope: str = "all"


class ArticleUpsertResponse(BaseModel):
    source_path: str
    created: bool


class ArticleListingItem(BaseModel):
    """Structured article metadata row returned by read-only listing APIs for corpus introspection workflows."""

    source_path: str
    filename: str
    title: str = ""
    authors: str = ""
    venue: str = ""
    published_date: str = ""
    doi: str = ""
    scope: str = "all"
    comments: str = ""
    updated_at: str = ""
    abstract: str | None = None


class ArticleListingResponse(BaseModel):
    """Container for article-listing results with total row count and normalized scope filter context."""

    articles: list[ArticleListingItem]
    count: int
    scopes_queried: list[str]


class ScopeRegisterRequest(BaseModel):
    """Register a new retrieval scope at runtime."""

    name: str
    prefixes: list[str]
    description: str = ""
    watch: bool = False
    force: bool = False


class ScopeRegisterResponse(BaseModel):
    name: str
    created: bool
    watching: list[str]


class RefreshCorpusHintsRequest(BaseModel):
    """Per-scope corpus hints refresh with optional tuning parameters."""

    scope: str | None = None
    entity_boost_hyphen: float = 1.3
    entity_boost_single: float = 1.2
    blocklist_override: list[str] | None = None
    extra_blocklist: list[str] | None = None


class RefreshCorpusHintsResponse(BaseModel):
    scopes_updated: list[str]
    terms_by_scope: dict[str, int]


class PrefixCoverage(BaseModel):
    """Coverage stats for a single scope prefix path."""

    path: str
    indexed_files: int
    last_indexed: str | None = None


class ScopeCoverage(BaseModel):
    """Aggregated coverage for a named retrieval scope."""

    prefixes: list[PrefixCoverage]
    total_indexed: int


class CoverageResponse(BaseModel):
    """Per-scope, per-prefix indexed file counts and recency."""

    scopes: dict[str, ScopeCoverage]
    timestamp_scan_degraded: bool = False
