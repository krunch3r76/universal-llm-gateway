"""Search, indexing, stats, and scope listing DTOs for the RAG HTTP API."""

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
    sparse_only: bool = False
    # Pre-computed embedding vector; when provided, skip the embed_query() call.
    # Used by pipeline handlers that batch-embed all queries in a single forward pass.
    query_embedding: list[float] | None = None
    # Provenance-tier distance multipliers for citation-critical corpora (e.g. legal).
    # Keys are provenance_tier metadata values (e.g. "court_record", "regulator_pub",
    # "practitioner_analysis", "expert_commentary"); values are distance multipliers
    # applied at search time — values < 1.0 boost (reduce distance), 1.0 = neutral.
    # Chunks without a matching provenance_tier tag are unaffected.
    tier_weight: dict[str, float] | None = None

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
    indexed_sources_count: int = 0
    property_index_available: bool = True
    indexing_failures_permanent_count: int = 0
    indexing_failures_transient_count: int = 0
    contextualize_cache_rows: int = 0
    contextualize_cache_rows_degraded: bool = False
    stale_corpus_hints_count: int = 0
    stale_corpus_hints_count_degraded: bool = False


class IndexingFailureResponse(BaseModel):
    """File-level indexing failure row exposed via the admin API."""

    source: str
    failure_category: str
    failure_reason: str
    error_message: str
    error_type: str
    first_failed_at: str
    last_failed_at: str
    attempt_count: int
    source_hash: str | None = None
    source_size_bytes: int | None = None
    source_mtime_ns: int | None = None


class IndexingFailuresListResponse(BaseModel):
    failures: list[IndexingFailureResponse]
    count: int


class DeleteIndexingFailureResponse(BaseModel):
    source: str
    deleted: bool


class RetryIndexingFailureResponse(BaseModel):
    source: str
    cleared: bool
    scheduled: bool


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
    article_count: int = 0
    top_topics: list[str] = Field(default_factory=list)


class ScopesResponse(BaseModel):
    scopes: dict[str, ScopeInfo]
