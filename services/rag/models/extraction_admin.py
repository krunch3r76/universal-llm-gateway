"""Extraction export, chunk admin, indexing failures, and extraction queue DTOs.

Pydantic request/response models for the RAG service's admin and extraction
endpoints, re-exported by ``services.rag.models``. Consumers include
``admin_routes`` (``/extraction_export``, ``/directory``, source delete,
``/clear_directory``) and ``rag_service/api.py`` (``/chunks_by_index``,
``/extraction/queue``, ``/extraction/failed``). Pure data shapes: no logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExtractionExportItem(BaseModel):
    """Single chunk with its extraction metadata, for bulk export.

    One row of ``GET /extraction_export``, read straight from Chroma metadata:
    the extraction JSON string, model, and schema version are ``None`` when the
    chunk has no extraction; ``text`` is empty unless ``include_text=true``.
    """

    source: str
    chunk_id: str
    chunk_index: int
    text: str
    extraction: str | None = None
    extraction_model: str | None = None
    extraction_schema_version: str | None = None


class ExtractionExportResponse(BaseModel):
    """Bulk extraction export: all chunks under a source prefix.

    Response of ``GET /extraction_export`` (``prefix`` filter is optional);
    ``items`` are sorted by source and chunk index, and ``total_chunks`` and
    ``total_sources`` summarize them for extraction-coverage audits.
    """

    total_chunks: int
    total_sources: int
    items: list[ExtractionExportItem]


class SourceDeleteResponse(BaseModel):
    """Result of deleting a single source from all storage surfaces.

    Returned by ``DELETE /source`` (MCP op ``delete_source``). Counts
    removals from Chroma chunks, the FTS index, and property rows, and flags
    whether the linked article record was deleted too.
    """

    source: str
    chunks_deleted: int
    fts_removed: int
    properties_removed: int
    article_deleted: bool


class DirectoryDeleteResponse(BaseModel):
    """Result of deleting all sources under a directory prefix.

    Returned by ``DELETE /directory`` (MCP op ``delete_directory``). Directory
    analogue of ``SourceDeleteResponse``: aggregated counts across sources,
    chunks, FTS rows, and articles removed under ``path``.
    """

    path: str
    sources_deleted: int
    chunks_deleted: int
    fts_removed: int
    articles_deleted: int


class ClearDirectoryRequest(BaseModel):
    """Request body for ``POST /clear_directory``: the directory to purge.

    ``path`` is validated as a directory; every source beneath it loses its
    chunks and property index entries, typically before a fresh re-index.
    """

    path: str


class ClearDirectoryResponse(BaseModel):
    """Counts returned by ``POST /clear_directory`` after purging a directory.

    ``sources_cleared`` and ``chunks_cleared`` mirror the payload of the
    ``rag_directory_cleared`` event published by the same route.
    """

    sources_cleared: int
    chunks_cleared: int


class ChunkIndexGroup(BaseModel):
    """A source file and the chunk indices to fetch from it.

    One group inside ``ChunksByIndexRequest``; indices are positional
    ``chunk_index`` values within ``source``. Groups with no indices are
    skipped by the route.
    """

    source: str
    chunk_indices: list[int]


class ChunkByIndexItem(BaseModel):
    """A single chunk returned by the chunks_by_index endpoint.

    Carries the Chroma chunk ID, owning source, positional ``chunk_index``,
    chunk text, and the raw stored metadata dict for that chunk.
    """

    chunk_id: str
    source: str
    chunk_index: int
    text: str
    metadata: dict[str, Any]


class ChunksByIndexRequest(BaseModel):
    """Batched request to fetch chunks by source + index position.

    Body of ``POST /chunks_by_index``; gives deterministic source/index
    addressing across several sources in one call, as opposed to semantic
    top-k retrieval via ``/search``.
    """

    groups: list[ChunkIndexGroup]


class ChunksByIndexResponse(BaseModel):
    """Response from chunks_by_index endpoint.

    Flat list of ``ChunkByIndexItem`` results for ``POST /chunks_by_index``;
    requested indices that are not stored are simply absent from the list.
    """

    chunks: list[ChunkByIndexItem]


class FailedChunkItem(BaseModel):
    """One persisted chunk-level LLM extraction failure record.

    Built from ``PropertyIndex.get_failed_chunks`` rows for
    ``GET /extraction/failed``: error text, optional parse failure reason,
    attempt count, and the timestamp when the failure was recorded.
    """

    chunk_id: str
    source: str
    error: str
    parse_failure_reason: str | None = None
    attempt_count: int
    recorded_at: str


class FailedExtractionResponse(BaseModel):
    """Response of ``GET /extraction/failed`` listing failed extraction chunks.

    ``total`` equals ``len(chunks)``; results cover every source unless the
    request filtered by a single ``source`` path.
    """

    total: int
    chunks: list[FailedChunkItem]


class ExtractionQueueBreakdownModel(BaseModel):
    """Per-state bucket counts for the extraction queue in ``/extraction/queue``.

    Mirrors ``PropertyIndex.get_extraction_queue_breakdown``: ready, in-flight,
    cooling off after failure, capacity blocked, and retry-exhausted sources.
    All zeros when the property index is unavailable.
    """

    total: int
    ready: int
    in_flight: int
    cooling_off: int
    capacity_blocked: int
    exhausted: int


class ExtractionQueueRowModel(BaseModel):
    """One source-level row of the extraction queue with retry diagnostics.

    Mapped from ``PropertyIndex.list_extraction_queue_rows``: queue time,
    attempt count, last attempt and failure details (error, type, category),
    and the derived queue ``state`` label.
    """

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
    """Response of ``GET /extraction/queue``: bucket summary plus sample rows.

    ``breakdown`` counts the whole queue while ``rows`` is capped by the
    route's ``limit`` query parameter (default 100).
    """

    breakdown: ExtractionQueueBreakdownModel
    rows: list[ExtractionQueueRowModel]
