from __future__ import annotations

from typing import Any

from pydantic import BaseModel

DECAY_LAMBDA = 0.01  # half-life ~= 69 days


class SearchRequest(BaseModel):
    """Request body for RAG /search. Scope and source_prefixes are mutually exclusive."""

    query: str
    top_k: int = 5
    recency_weight: float = 0.0
    max_distance: float | None = None  # None = return all (backward compat)
    source_prefixes: list[str] | None = None
    scope: str | list[str] | None = (
        None  # Single scope name or list of names; resolved to union of source_prefixes via config; mutually exclusive with source_prefixes
    )


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


class FailedChunkItem(BaseModel):
    chunk_id: str
    source: str
    error: str
    attempt_count: int
    recorded_at: str


class FailedExtractionResponse(BaseModel):
    total: int
    chunks: list[FailedChunkItem]
