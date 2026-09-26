"""Search, indexing, stats, and scope listing DTOs for the RAG HTTP API.

Pydantic shapes re-exported by ``services.rag.models``: ``/search`` request and
response (consumed by ``rag_service/search.py``), file/directory indexing
results from ``admin_routes/indexing.py`` and the indexing pipeline, indexing
status and failure rows (``admin_routes/status.py``, ``failures.py``), and
``/scopes`` listing. Also defines ``RECENCY_DECAY_LAMBDA`` used by
``search_scope/recency.py``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

# Decay constant for recency scoring; half-life ≈ 69 days.
RECENCY_DECAY_LAMBDA = 0.01


class SearchRequest(BaseModel):
    """Request body for RAG /search. Scope and source_prefixes are mutually exclusive.

    Executed by ``search.execute_search``. ``top_k`` caps results,
    ``max_distance`` filters on raw cosine distance, ``recency_weight`` enables
    age-decay reordering, ``sparse_only`` skips the dense vector query, and
    ``tier_weight`` rescales distances by provenance tier. Setting both
    ``scope`` and ``source_prefixes`` raises a validation error.
    """

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
    """Ranked ``/search`` results as parallel lists of chunk text, metadata, distance.

    Index i of ``chunks``, ``metadata`` and ``distances`` describes the same
    chunk; ``property_hits`` counts results matched by the property-index boost
    (0 when no boost applied).
    """

    chunks: list[str]
    metadata: list[dict[str, Any]]
    distances: list[float]
    property_hits: int = 0


class IndexRequest(BaseModel):
    """Request body for ``POST /index`` and ``POST /reindex`` on a single file.

    ``metadata_overrides`` are merged into every chunk's stored metadata;
    ``force`` re-chunks unchanged content and skips the PDF duplicate check.
    """

    path: str
    metadata_overrides: dict[str, str | int | float | bool] | None = None
    force: bool = False


class IndexResult(BaseModel):
    """Outcome of indexing one file, from ``/index``, ``/reindex``, or the watcher.

    ``indexed``/``deleted`` count chunks written and removed; ``unchanged``
    means nothing was rewritten (cached source matched, or entity-gated skip).
    ``duplicate`` and ``duplicate_of`` flag a PDF already indexed under another
    path; extraction counters report entities and topics produced.
    """

    indexed: int
    deleted: int
    unchanged: bool
    file: str
    duplicate: bool = False
    duplicate_of: str | None = None
    extraction_entities: int = 0
    extraction_topics: int = 0


class DeleteResult(BaseModel):
    """Chunk-removal outcome for one source file dropped from the vector store.

    Returned by ``rag_service/indexing/delete.py`` and the watcher delete
    callback in ``watcher_runtime.py`` when a watched file disappears.
    """

    file: str
    deleted: int


class IndexDirectoryRequest(BaseModel):
    """Request body for ``POST /index_directory`` and ``POST /reindex_directory``.

    Indexes files under ``path``, optionally limited to ``extensions``; overrides
    and ``force`` apply to every file as in ``IndexRequest``.
    """

    path: str
    extensions: list[str] | None = None
    metadata_overrides: dict[str, str | int | float | bool] | None = None
    force: bool = False


class IndexDirectoryResponse(BaseModel):
    """Aggregate totals for a directory (re)index run across all matched files.

    ``indexed``/``deleted`` are chunk counts; ``unchanged``, ``files``, and
    ``duplicates`` are file counts summed from ``index_directory_contents``.
    """

    indexed: int
    deleted: int
    unchanged: int
    files: int
    duplicates: int = 0


class StatsResponse(BaseModel):
    """Response of ``GET /stats``: total chunk count in the Chroma collection.

    ``collection`` names the Chroma collection the count was taken from.
    """

    count: int
    collection: str


class WatcherStatusItem(BaseModel):
    """Single watcher state row from WatcherManager status output.

    Embedded in ``IndexingStatusResponse.watchers``: watched ``path``, whether
    it is enabled, and cumulative reload and error counters for that watcher.
    """

    path: str
    enabled: bool
    reload_count: int
    error_count: int


class IndexingStatusResponse(BaseModel):
    """Unified indexing health payload for operator-facing status clients.

    Returned by ``GET /indexing/status``. Combines the pending-file backlog and
    a bounded sample, Chroma chunk count and availability, watcher rows,
    extraction and indexing failure counts, and cache/hint counters. The
    ``*_degraded`` flags mark counters that could not be read reliably.
    """

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
    """File-level indexing failure row exposed via the admin API.

    Serialized from ``PropertyIndex.list_indexing_failures`` for
    ``GET /indexing_failures``: permanent-vs-transient ``failure_category``,
    reason and error details, first/last failure times, attempt count, and the
    source hash, size, and mtime captured when it failed.
    """

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
    """Response of ``GET /indexing_failures``, optionally filtered by category.

    ``count`` equals ``len(failures)``; ``category`` accepts ``all``,
    ``permanent``, or ``transient``.
    """

    failures: list[IndexingFailureResponse]
    count: int


class DeleteIndexingFailureResponse(BaseModel):
    """Confirmation from ``DELETE /indexing_failures/{source}`` of a cleared row.

    ``deleted`` is always true on success; an unknown source returns 404 instead.
    """

    source: str
    deleted: bool


class RetryIndexingFailureResponse(BaseModel):
    """Result of ``POST /indexing_failures/{source}/retry`` for a failed file.

    ``cleared`` reports whether a failure row was removed; ``scheduled`` reports
    whether the WatcherManager accepted a reindex request for the source.
    """

    source: str
    cleared: bool
    scheduled: bool


class ClearResponse(BaseModel):
    """Collection-wide clear result: number of chunks deleted and collection name.

    Re-exported from ``services.rag.models`` alongside ``StatsResponse``; the
    per-directory variant is ``ClearDirectoryResponse``.
    """

    deleted: int
    collection: str


class SourceResponse(BaseModel):
    """All stored chunks for one source file from ``GET /source``, in order.

    ``chunks`` and ``metadata`` are parallel lists sorted by ``chunk_index``;
    an unindexed path yields 404 rather than an empty response.
    """

    chunks: list[str]
    metadata: list[dict[str, str | int | float | bool]]


class SourcesResponse(BaseModel):
    """List of source file paths (e.g. for extraction export by prefix).

    Returned by ``GET /sources``; paths are distinct sources from the property
    index, optionally filtered by ``prefix``, and empty when the index is down.
    """

    sources: list[str]


class ScopeInfo(BaseModel):
    """Description of one configured search scope in the ``/scopes`` listing.

    ``prefixes`` and ``description`` come from RAG config; ``article_count``
    and ``top_topics`` are enrichment from the property index when available.
    """

    prefixes: list[str]
    description: str
    article_count: int = 0
    top_topics: list[str] = Field(default_factory=list)


class ScopesResponse(BaseModel):
    """Response of ``GET /scopes`` (MCP op ``list_scopes``): scope name to info.

    Keys are scope names usable as ``SearchRequest.scope``; values are
    ``ScopeInfo`` entries.
    """

    scopes: dict[str, ScopeInfo]
