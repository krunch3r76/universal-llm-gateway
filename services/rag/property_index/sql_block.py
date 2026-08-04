"""Property index: DDL / migration SQL strings."""

from __future__ import annotations

_V1_BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS properties (
    key TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'all',
    source TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (key, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_key ON properties(key);
CREATE INDEX IF NOT EXISTS idx_chunk ON properties(chunk_id);

CREATE TABLE IF NOT EXISTS pending (
    file TEXT NOT NULL PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS failed_extractions (
    chunk_id TEXT NOT NULL PRIMARY KEY,
    source TEXT NOT NULL,
    error TEXT NOT NULL,
    parse_failure_reason TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    permanent INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_failed_source ON failed_extractions(source);
CREATE TABLE IF NOT EXISTS watermarks (
    step TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_V2_METADATA_SQL = """
CREATE TABLE IF NOT EXISTS corpus_hints (
    scope TEXT NOT NULL,
    term TEXT NOT NULL,
    score REAL NOT NULL,
    prefix TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, term)
);
CREATE INDEX IF NOT EXISTS idx_corpus_hints_scope ON corpus_hints(scope);

CREATE TABLE IF NOT EXISTS scope_vocabulary (
    scope TEXT NOT NULL,
    register TEXT NOT NULL,
    term TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scope, register, term)
);
CREATE INDEX IF NOT EXISTS idx_scope_vocabulary_scope ON scope_vocabulary(scope);

CREATE TABLE IF NOT EXISTS articles (
    source_path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    published_date TEXT NOT NULL DEFAULT '',
    doi TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'all',
    content_hash TEXT NOT NULL DEFAULT '',
    subdirectory TEXT NOT NULL DEFAULT '',
    comments TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_articles_scope ON articles(scope);
"""

_V4_SOURCE_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS indexed_sources (
    source TEXT PRIMARY KEY,
    mtime_ns INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    extraction_schema_version INTEGER NOT NULL,
    extraction_model TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_indexed_sources_updated_at ON indexed_sources(updated_at);
"""

_V8_EXTRACTION_QUEUE_SQL = """
CREATE TABLE IF NOT EXISTS extraction_queue (
    source TEXT PRIMARY KEY,
    queued_at TEXT NOT NULL DEFAULT (datetime('now')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    last_error TEXT,
    last_error_type TEXT,
    last_failure_category TEXT,
    last_failure_at TEXT
);
"""

_V9_INDEXING_FAILURES_SQL = """
CREATE TABLE IF NOT EXISTS indexing_failures (
    source TEXT PRIMARY KEY,
    failure_category TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    error_message TEXT NOT NULL,
    error_type TEXT NOT NULL,
    first_failed_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_failed_at TEXT NOT NULL DEFAULT (datetime('now')),
    attempt_count INTEGER NOT NULL DEFAULT 1,
    source_hash TEXT,
    source_size_bytes INTEGER,
    source_mtime_ns INTEGER
);
CREATE INDEX IF NOT EXISTS idx_indexing_failures_category
    ON indexing_failures(failure_category);
CREATE INDEX IF NOT EXISTS idx_indexing_failures_last_failed
    ON indexing_failures(last_failed_at);
"""

_V10_CONTEXTUALIZED_CHUNKS_SQL = """
CREATE TABLE IF NOT EXISTS contextualized_chunks (
    source_hash TEXT NOT NULL CHECK(source_hash <> ''),
    chunk_hash TEXT NOT NULL CHECK(chunk_hash <> ''),
    contextualize_model TEXT NOT NULL CHECK(contextualize_model <> ''),
    contextualize_schema_version TEXT NOT NULL
        CHECK(contextualize_schema_version <> ''),
    context_prefix TEXT NOT NULL CHECK(context_prefix <> ''),
    cached_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (
        source_hash, chunk_hash,
        contextualize_model, contextualize_schema_version
    )
);
CREATE INDEX IF NOT EXISTS idx_contextualized_source
    ON contextualized_chunks(source_hash);
"""

_V16_CONTEXTUALIZED_CHUNKS_G1_SQL = """
CREATE TABLE IF NOT EXISTS contextualized_chunks_g1 (
    source_identity TEXT NOT NULL CHECK(source_identity <> ''),
    chunk_hash TEXT NOT NULL CHECK(chunk_hash <> ''),
    neighbor_digest TEXT NOT NULL CHECK(neighbor_digest <> ''),
    contextualize_model TEXT NOT NULL CHECK(contextualize_model <> ''),
    contextualize_schema_version TEXT NOT NULL
        CHECK(contextualize_schema_version <> ''),
    context_prefix TEXT NOT NULL CHECK(context_prefix <> ''),
    cached_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (
        source_identity, chunk_hash, neighbor_digest,
        contextualize_model, contextualize_schema_version
    )
);
CREATE INDEX IF NOT EXISTS idx_contextualized_g1_source
    ON contextualized_chunks_g1(source_identity);
"""

_V14_EXTRACTION_QUEUE_EXECUTION_ID_SQL = """
ALTER TABLE extraction_queue ADD COLUMN active_execution_id TEXT;
"""

_V15_SKILL_VOCABULARY_SQL = """
CREATE TABLE IF NOT EXISTS skill_vocabulary (
    slug TEXT NOT NULL,
    register TEXT NOT NULL,
    term TEXT NOT NULL,
    score REAL NOT NULL,
    chunk_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (slug, register, term)
);
CREATE INDEX IF NOT EXISTS idx_skill_vocabulary_slug ON skill_vocabulary(slug);
"""

_V12_CONTEXTUALIZATION_EXCEPTIONS_SQL = """
CREATE TABLE IF NOT EXISTS contextualization_exceptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_hash TEXT,
    contextualize_model TEXT NOT NULL,
    operation_id TEXT,
    total_chunks INTEGER NOT NULL,
    cache_miss_chunks INTEGER NOT NULL,
    successful_chunks INTEGER NOT NULL,
    failed_chunks INTEGER NOT NULL,
    abandoned_chunks INTEGER NOT NULL,
    abandoned_indices_json TEXT NOT NULL,
    request_ids_json TEXT NOT NULL,
    first_failure TEXT NOT NULL,
    idle_seconds REAL,
    tail_idle_timeout_s REAL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_contextualization_exceptions_source
    ON contextualization_exceptions(source, recorded_at);
CREATE INDEX IF NOT EXISTS idx_contextualization_exceptions_recorded_at
    ON contextualization_exceptions(recorded_at);
"""

_V5_SCOPE_FRESHNESS_SQL = """
CREATE TABLE IF NOT EXISTS scope_freshness (
    scope TEXT PRIMARY KEY,
    files_hash TEXT NOT NULL,
    classified_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_SCHEMA_VERSION_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL
);
"""


__all__ = [
    "_V1_BASELINE_SQL",
    "_V2_METADATA_SQL",
    "_V4_SOURCE_CACHE_SQL",
    "_V8_EXTRACTION_QUEUE_SQL",
    "_V9_INDEXING_FAILURES_SQL",
    "_V10_CONTEXTUALIZED_CHUNKS_SQL",
    "_V16_CONTEXTUALIZED_CHUNKS_G1_SQL",
    "_V12_CONTEXTUALIZATION_EXCEPTIONS_SQL",
    "_V14_EXTRACTION_QUEUE_EXECUTION_ID_SQL",
    "_V15_SKILL_VOCABULARY_SQL",
    "_V5_SCOPE_FRESHNESS_SQL",
    "_CREATE_SCHEMA_VERSION_SQL",
]
