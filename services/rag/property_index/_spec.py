"""SQLite-backed property inverted index for structured RAG.

Maps property keys (prop.name@@stargate, prop.topic@@federation) to chunk IDs.
Enables exact entity/topic lookups that complement vector similarity search.

Concurrency: all writes serialized via SequentialExecutor (no locks).
Reads go directly to SQLite — safe with a single writer.

Key format: prop.{category}@@{value} (case-normalized via .lower()).

Failed extractions are recorded in the ``failed_extractions`` table so callers
can inspect structural failures (e.g. max_tokens exceeded) without tailing logs.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from universal_event_bus.actor.sequential import SequentialExecutor

from services.rag.article_registry import ArticleEntry
from services.rag.contextualize_cache import StoredContextRow
from services.rag.fts_index import FtsIndex

_SQLITE_MAX_VARIABLES = 900  # conservative bound under SQLite's 999 limit

logger = logging.getLogger("services.rag.property_index")

_DEFAULT_DB_PATH = Path.home() / ".rag" / "store" / "rag_metadata.db"
_LEGACY_DB_PATH = Path.home() / ".rag" / "store" / "property_index.db"


def _row_str(value: object) -> str:
    return "" if value is None else str(value)


@dataclass(slots=True, kw_only=True)
class FailedChunk:
    chunk_id: str
    source: str
    error: str
    parse_failure_reason: str | None
    attempt_count: int
    permanent: bool
    recorded_at: str


@dataclass(slots=True, kw_only=True)
class PendingSnapshot:
    """Bounded view of pending journal rows for operational status APIs."""

    count: int
    sample: list[str]


@dataclass(slots=True, kw_only=True)
class FailureSnapshot:
    """Count view of failed extraction rows used by status/health endpoints."""

    failed_extractions_count: int
    failed_extractions_permanent_count: int


@dataclass(slots=True, kw_only=True)
class IndexingFailure:
    """File-level indexing failure row from ``indexing_failures``."""

    source: str
    failure_category: str
    failure_reason: str
    error_message: str
    error_type: str
    first_failed_at: str
    last_failed_at: str
    attempt_count: int
    source_hash: str | None
    source_size_bytes: int | None
    source_mtime_ns: int | None


@dataclass(slots=True, kw_only=True)
class ContextualizationException:
    """Durable record of a successful-but-degraded contextualization attempt."""

    id: int
    source: str
    source_hash: str | None
    contextualize_model: str
    operation_id: str | None
    total_chunks: int
    cache_miss_chunks: int
    successful_chunks: int
    failed_chunks: int
    abandoned_chunks: int
    abandoned_indices: list[int]
    request_ids: dict[int, str]
    first_failure: str
    idle_seconds: float | None
    tail_idle_timeout_s: float | None
    recorded_at: str


@dataclass(slots=True, kw_only=True)
class ExtractionQueueClaim:
    """One source row atomically claimed by the extraction worker."""

    source: str
    attempts: int
    queued_at: str
    claimed_at: str


@dataclass(slots=True, kw_only=True)
class RecoveredExtractionClaim:
    """One abandoned source claim recovered during startup."""

    source: str
    attempts: int
    queued_at: str
    claimed_at: str
    claimed_age_seconds: float
    active_execution_id: str | None = None


@dataclass(slots=True, kw_only=True)
class ExtractionQueueBreakdown:
    """Aggregate extraction queue buckets for health and status endpoints."""

    total: int
    ready: int
    in_flight: int
    cooling_off: int
    capacity_blocked: int
    exhausted: int


@dataclass(slots=True, kw_only=True)
class ExtractionQueueRow:
    """One extraction queue row with computed operational state."""

    source: str
    queued_at: str
    attempts: int
    last_attempt_at: str | None
    last_error: str | None
    last_error_type: str | None
    last_failure_category: str | None
    last_failure_at: str | None
    state: str


@dataclass(slots=True, kw_only=True)
class IndexedSourceSnapshot:
    """Cached source freshness row used for stat-first unchanged checks.

    `source_hash` empty string ('') means content identity is unknown —
    typically a row that predates V11 backfill, or a source whose hash
    was unavailable at write time. Callers MUST treat '' as unknown,
    never as a reusable identity. Cache lookup, cache store, and orphan
    GC all skip rows with empty source_hash.
    """

    source: str
    mtime_ns: int
    size_bytes: int
    extraction_schema_version: int
    extraction_model: str
    updated_at: str
    source_hash: str = ""


from .sql_block import *  # noqa: E402,F401,F403

__all__ = [  # noqa: F405
    "ArticleEntry",
    "Callable",
    "ContextualizationException",
    "ExtractionQueueBreakdown",
    "ExtractionQueueClaim",
    "ExtractionQueueRow",
    "FailedChunk",
    "FailureSnapshot",
    "FtsIndex",
    "IndexedSourceSnapshot",
    "IndexingFailure",
    "Path",
    "PendingSnapshot",
    "RecoveredExtractionClaim",
    "SequentialExecutor",
    "StoredContextRow",
    "defaultdict",
    "json",
    "shutil",
    "sqlite3",
    "_CREATE_SCHEMA_VERSION_SQL",
    "_DEFAULT_DB_PATH",
    "_LEGACY_DB_PATH",
    "_SQLITE_MAX_VARIABLES",
    "_V10_CONTEXTUALIZED_CHUNKS_SQL",
    "_V12_CONTEXTUALIZATION_EXCEPTIONS_SQL",
    "_V14_EXTRACTION_QUEUE_EXECUTION_ID_SQL",
    "_V15_SKILL_VOCABULARY_SQL",
    "_V1_BASELINE_SQL",
    "_V2_METADATA_SQL",
    "_V4_SOURCE_CACHE_SQL",
    "_V5_SCOPE_FRESHNESS_SQL",
    "_V8_EXTRACTION_QUEUE_SQL",
    "_V9_INDEXING_FAILURES_SQL",
    "_row_str",
    "logger",
]
