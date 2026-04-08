-- Migration 002: Cortex v2 — chunks, surface forms, provenance
-- Applied: 2026-03-17
-- Description: Source provenance via chunks and surface forms, assertion.chunk_id

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_date DATETIME,
    observer TEXT NOT NULL DEFAULT 'web-claude',
    chunk_index INTEGER DEFAULT 0,
    extraction_run INTEGER,
    token_count INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_uri TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    model TEXT NOT NULL,
    chunk_count INTEGER DEFAULT 0,
    entity_count INTEGER DEFAULT 0,
    assertion_count INTEGER DEFAULT 0,
    surface_form_count INTEGER DEFAULT 0,
    error_log TEXT,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME,
    duration_ms INTEGER,
    content_hash TEXT
);

CREATE TABLE IF NOT EXISTS surface_forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mention TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    chunk_id INTEGER REFERENCES chunks(id),
    resolution_confidence REAL,
    resolution_reasoning TEXT,
    context_hash TEXT,
    mention_type TEXT DEFAULT 'name',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Add chunk_id and extraction_run to assertions
ALTER TABLE assertions ADD COLUMN chunk_id INTEGER REFERENCES chunks(id);
ALTER TABLE assertions ADD COLUMN extraction_run INTEGER;
ALTER TABLE assertions ADD COLUMN is_atomic BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE assertions ADD COLUMN is_decontextualized BOOLEAN NOT NULL DEFAULT TRUE;

-- Add chunk_id to relationships
ALTER TABLE relationships ADD COLUMN chunk_id INTEGER REFERENCES chunks(id);

CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_uri);
CREATE INDEX IF NOT EXISTS idx_chunks_observer ON chunks(observer);
CREATE INDEX IF NOT EXISTS idx_sf_mention ON surface_forms(mention);
CREATE INDEX IF NOT EXISTS idx_sf_entity ON surface_forms(entity_id);
CREATE INDEX IF NOT EXISTS idx_sf_context ON surface_forms(context_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sf_cache ON surface_forms(mention, context_hash);
CREATE INDEX IF NOT EXISTS idx_assertions_chunk ON assertions(chunk_id);
CREATE INDEX IF NOT EXISTS idx_extruns_status ON extraction_runs(status);
CREATE INDEX IF NOT EXISTS idx_extruns_source ON extraction_runs(source_uri);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (2, 'Cortex v2: chunks, surface_forms, assertions.chunk_id — genotype/phenotype entity resolution');
