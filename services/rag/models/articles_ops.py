"""Article metadata, embed/rerank, corpus hints refresh, and coverage DTOs.

Pydantic request/response models for RAG admin and query endpoints, re-exported
through ``services.rag.models``. Consumers include ``admin_routes.articles``
(``/articles``, ``/article``, ``/refresh_corpus_hints``), ``admin_routes.status``
(``/coverage``, ``/source-status``) and ``rag_service.api`` (``/embed_batch``,
``/scopes``). ``PipelineStage`` names a source's position in the ingest pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

PipelineStage = Literal["registered", "queued", "chunked", "contextualized"]


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
    """Result of POST /article: whether the row was created plus pipeline state.

    Built by ``admin_routes.articles.upsert_article``; reports the source's
    ``pipeline_stage``, precise extraction ``queue_state`` when queued, overall
    extraction queue depth and a ``frontier_status`` (currently "unknown").
    """

    source_path: str
    created: bool
    pipeline_stage: PipelineStage
    # Precise extraction_queue state when pipeline_stage == "queued", else None.
    queue_state: str | None
    # Total items currently in extraction_queue (all sources).
    queue_depth: int
    frontier_status: Literal["reachable", "unreachable", "unknown"]


class ArticleListingItem(BaseModel):
    """Structured article metadata row returned by read-only listing APIs for corpus introspection workflows.

    One element of ``ArticleListingResponse.articles`` from GET /articles in
    ``admin_routes.articles``; ``abstract`` is None unless ``include_abstract``
    is set, and None fields are excluded from the serialized response.
    """

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
    """Container for article-listing results with total row count and normalized scope filter context.

    Response model of GET /articles; ``scopes_queried`` echoes the scope filter
    after normalization so callers can see which scopes were actually searched.
    """

    articles: list[ArticleListingItem]
    count: int
    scopes_queried: list[str]


class ScopeRegisterRequest(BaseModel):
    """Register a new retrieval scope at runtime via POST /scopes.

    Handled by ``rag_service.api.register_scope``: ``prefixes`` are path
    prefixes defining the scope, ``watch`` asks for file watchers on them, and
    ``force`` overwrites an existing scope instead of returning HTTP 409. The
    scope is also persisted to rag.yaml.
    """

    name: str
    prefixes: list[str]
    description: str = ""
    watch: bool = False
    force: bool = False


class ScopeRegisterResponse(BaseModel):
    """Result of POST /scopes returned by ``rag_service.api.register_scope``.

    ``created`` is False when the scope name already existed; ``watching`` lists
    the prefix directories that now have active file watchers.
    """

    name: str
    created: bool
    watching: list[str]


class EmbedBatchRequest(BaseModel):
    """Batch-embed multiple query texts in a single forward pass.

    Request body for POST /embed_batch in ``rag_service.api``; the optional
    ``scope`` (name or list of names) is passed through to
    ``embed_queries_batch``. Transient embedding errors surface as HTTP 503.
    """

    texts: list[str]
    scope: str | list[str] | None = None


class EmbedBatchResponse(BaseModel):
    """Batch embedding results — one embedding vector per input text.

    Returned by POST /embed_batch; ``embeddings`` preserves the order of
    ``EmbedBatchRequest.texts`` so callers can zip inputs with vectors.
    """

    embeddings: list[list[float]]


class RerankRequest(BaseModel):
    """Cross-encoder reranking: score (query, passage) pairs.

    Rerank request DTO re-exported from ``services.rag.models``; one ``query`` is
    scored against every entry in ``passages``. Not bound to a RAG route here.
    """

    query: str
    passages: list[str]


class RerankResponse(BaseModel):
    """Cross-encoder scores — one per passage, same order as input.

    Pairs with ``RerankRequest``; ``model`` names the cross-encoder that
    produced ``scores`` so clients can detect reranker changes.
    """

    scores: list[float]
    model: str


class RefreshCorpusHintsRequest(BaseModel):
    """Per-scope corpus hints refresh with optional tuning parameters.

    Body of POST /refresh_corpus_hints in ``admin_routes.articles``. ``scope``
    None refreshes all configured scopes (an unknown name is HTTP 400); entity
    boost factors weight hyphenated vs single-word terms, and the blocklist
    fields replace or extend the default term blocklist.
    """

    scope: str | None = None
    entity_boost_hyphen: float = 1.3
    entity_boost_single: float = 1.2
    blocklist_override: list[str] | None = None
    extra_blocklist: list[str] | None = None


class RefreshCorpusHintsResponse(BaseModel):
    """Result of POST /refresh_corpus_hints listing which scopes were rebuilt.

    ``terms_by_scope`` maps each refreshed scope name to the number of hint
    terms written for it.
    """

    scopes_updated: list[str]
    terms_by_scope: dict[str, int]


class PrefixCoverage(BaseModel):
    """Coverage stats for a single scope prefix path.

    Nested in ``ScopeCoverage`` for GET /coverage: count of indexed files under
    ``path`` and the latest ``indexed_sources.updated_at`` timestamp, if known.
    """

    path: str
    indexed_files: int
    last_indexed: str | None = None


class ScopeCoverage(BaseModel):
    """Aggregated coverage for a named retrieval scope.

    Value type of ``CoverageResponse.scopes``; holds one ``PrefixCoverage`` per
    configured prefix and the scope-wide ``total_indexed`` file count.
    """

    prefixes: list[PrefixCoverage]
    total_indexed: int


class ArticleStatusRow(BaseModel):
    """Article metadata row surfaced on pipeline status reads.

    Built by ``admin_routes._helpers`` from the articles table and attached to
    ``SourceStatusItem.article`` in GET /source-status responses.
    """

    source_path: str
    filename: str = ""
    title: str = ""
    authors: str = ""
    venue: str = ""
    published_date: str = ""
    doi: str = ""
    scope: str = "all"
    content_hash: str = ""
    subdirectory: str = ""


class SourceStatusItem(BaseModel):
    """Pipeline state for one source file.

    Element of ``SourceStatusResponse.sources``: ``pipeline_stage`` derived
    from live SQLite state, extraction queue position/attempts/last error,
    indexing timestamp, contextualized chunk count and filesystem presence.
    """

    source_path: str
    pipeline_stage: PipelineStage
    # Precise extraction_queue state when pipeline_stage == "queued", else None.
    queue_state: str | None
    queue_position: int | None
    queue_attempts: int
    last_error: str | None
    indexed_at: str | None
    contextualized_chunks: int
    file_exists: bool
    article: ArticleStatusRow | None = None


class SourceStatusResponse(BaseModel):
    """Multi-source pipeline status snapshot with aggregate health fields.

    Response model of GET /source-status in ``admin_routes.status``; adds the
    extraction ``queue_depth`` and ``stale_corpus_hints_count`` (scopes whose
    corpus hints are stale) alongside per-source items.
    """

    sources: list[SourceStatusItem]
    queue_depth: int
    frontier_status: Literal["reachable", "unreachable", "unknown"]
    stale_corpus_hints_count: int


class CoverageResponse(BaseModel):
    """Per-scope, per-prefix indexed file counts and recency.

    Response model of GET /coverage in ``admin_routes.status``;
    ``timestamp_scan_degraded`` is True when last-indexed timestamps could not
    be read from the property index.
    """

    scopes: dict[str, ScopeCoverage]
    timestamp_scan_degraded: bool = False
