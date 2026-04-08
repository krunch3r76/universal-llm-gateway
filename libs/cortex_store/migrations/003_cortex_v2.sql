-- Migration 003: Cortex v2 — provenance, temporal, staging
--
-- Extends existing tables (entities, chunks, surface_forms, assertions)
-- with v2 fields for full provenance tracking, temporal modeling, and
-- extraction staging.
--
-- NOTE: Some columns may already exist from prior ad-hoc migrations.
-- The migration runner handles "duplicate column name" errors gracefully.

-- === ENTITIES EXTENSIONS ===
ALTER TABLE entities ADD COLUMN description TEXT;
ALTER TABLE entities ADD COLUMN status TEXT DEFAULT 'confirmed'
    CHECK(status IN ('confirmed', 'provisional', 'merged', 'deprecated'));

-- === CHUNKS EXTENSIONS (table exists from migration 002) ===
ALTER TABLE chunks ADD COLUMN source_hash TEXT;
ALTER TABLE chunks ADD COLUMN source_date DATE;
ALTER TABLE chunks ADD COLUMN chunk_index INTEGER;
ALTER TABLE chunks ADD COLUMN model_version TEXT;

-- === SURFACE_FORMS EXTENSIONS (table exists from migration 002) ===
ALTER TABLE surface_forms ADD COLUMN mention TEXT;
ALTER TABLE surface_forms ADD COLUMN span_start INTEGER;
ALTER TABLE surface_forms ADD COLUMN span_end INTEGER;
ALTER TABLE surface_forms ADD COLUMN context_hash TEXT;
ALTER TABLE surface_forms ADD COLUMN resolution_confidence REAL;
ALTER TABLE surface_forms ADD COLUMN resolution_reasoning TEXT;
ALTER TABLE surface_forms ADD COLUMN entity_type_hint TEXT;

-- === ASSERTIONS EXTENSIONS ===
-- chunk_id, human_reviewed, superseded_by, superseded_at already exist
ALTER TABLE assertions ADD COLUMN derivation_type TEXT
    CHECK(derivation_type IN ('quotation', 'compression', 'inference', 'other'));
ALTER TABLE assertions ADD COLUMN reasoning_summary TEXT;
ALTER TABLE assertions ADD COLUMN observed_at TEXT;
ALTER TABLE assertions ADD COLUMN valid_from TEXT;
ALTER TABLE assertions ADD COLUMN valid_until TEXT;
ALTER TABLE assertions ADD COLUMN validity_precision TEXT DEFAULT 'exact'
    CHECK(validity_precision IN ('exact', 'approximate', 'inferred'));
ALTER TABLE assertions ADD COLUMN confidence_score REAL;
ALTER TABLE assertions ADD COLUMN is_atomic INTEGER DEFAULT 1;
ALTER TABLE assertions ADD COLUMN is_decontextualized INTEGER DEFAULT 1;
ALTER TABLE assertions ADD COLUMN review_notes TEXT;
ALTER TABLE assertions ADD COLUMN temporal_type TEXT
    CHECK(temporal_type IN ('event', 'state', 'unknown'));

-- === STAGING TABLE ===
CREATE TABLE IF NOT EXISTS extraction_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_uri TEXT,
    proposal_type TEXT NOT NULL CHECK(proposal_type IN ('entity', 'assertion')),
    proposal_action TEXT NOT NULL DEFAULT 'add'
        CHECK(proposal_action IN ('add', 'revise', 'remove')),
    target_id TEXT,
    proposal_json TEXT NOT NULL,
    chunk_id INTEGER REFERENCES chunks(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'rejected', 'merged')),
    resolved_to TEXT,
    reviewer TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- === INDEXES ===
CREATE INDEX IF NOT EXISTS idx_assertions_temporal
    ON assertions(entity_id, valid_from, valid_until, superseded_by);
CREATE INDEX IF NOT EXISTS idx_assertions_review
    ON assertions(confidence, human_reviewed)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_staging_status
    ON extraction_staging(status);
CREATE INDEX IF NOT EXISTS idx_staging_source
    ON extraction_staging(source_uri);
CREATE INDEX IF NOT EXISTS idx_surface_forms_cache
    ON surface_forms(mention, context_hash);
