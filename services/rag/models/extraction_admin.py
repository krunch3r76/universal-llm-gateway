"""Extraction export, chunk admin, indexing failures, and extraction queue DTOs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
    parse_failure_reason: str | None = None
    attempt_count: int
    recorded_at: str


class FailedExtractionResponse(BaseModel):
    total: int
    chunks: list[FailedChunkItem]


class ExtractionQueueBreakdownModel(BaseModel):
    total: int
    ready: int
    in_flight: int
    cooling_off: int
    capacity_blocked: int
    exhausted: int


class ExtractionQueueRowModel(BaseModel):
    source: str
    queued_at: str
    attempts: int
    last_attempt_at: str | None = None
    last_error: str | None = None
    last_error_type: str | None = None
    last_failure_category: str | None = None
    last_failure_at: str | None = None
    state: str


class ExtractionQueueResponse(BaseModel):
    breakdown: ExtractionQueueBreakdownModel
    rows: list[ExtractionQueueRowModel]
