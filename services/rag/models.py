from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator

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
