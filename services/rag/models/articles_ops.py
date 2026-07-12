"""Article metadata, embed/rerank, corpus hints refresh, and coverage DTOs."""

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
    source_path: str
    created: bool
    pipeline_stage: PipelineStage
    # Precise extraction_queue state when pipeline_stage == "queued", else None.
    queue_state: str | None
    # Total items currently in extraction_queue (all sources).
    queue_depth: int
    frontier_status: Literal["reachable", "unreachable", "unknown"]


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


class EmbedBatchRequest(BaseModel):
    """Batch-embed multiple query texts in a single forward pass."""

    texts: list[str]
    scope: str | list[str] | None = None


class EmbedBatchResponse(BaseModel):
    """Batch embedding results — one embedding vector per input text."""

    embeddings: list[list[float]]


class RerankRequest(BaseModel):
    """Cross-encoder reranking: score (query, passage) pairs."""

    query: str
    passages: list[str]


class RerankResponse(BaseModel):
    """Cross-encoder scores — one per passage, same order as input."""

    scores: list[float]
    model: str


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


class ArticleStatusRow(BaseModel):
    """Article metadata row surfaced on pipeline status reads."""

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
    """Pipeline state for one source file."""

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
    """Multi-source pipeline status snapshot with aggregate health fields."""

    sources: list[SourceStatusItem]
    queue_depth: int
    frontier_status: Literal["reachable", "unreachable", "unknown"]
    stale_corpus_hints_count: int


class CoverageResponse(BaseModel):
    """Per-scope, per-prefix indexed file counts and recency."""

    scopes: dict[str, ScopeCoverage]
    timestamp_scan_degraded: bool = False
