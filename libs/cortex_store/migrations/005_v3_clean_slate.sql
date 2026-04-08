-- Migration 005: v3.0 clean-slate migration
-- Applied: 2026-03-22
-- Description: Rebuild all indexes with v3_ prefix after data migration.
-- This migration ran after the v3 data migration (003+004) populated
-- the new schema. It drops legacy indexes and creates the v3 set.

-- Views
CREATE VIEW IF NOT EXISTS current_entities AS
SELECT *
FROM entities
WHERE status NOT IN ('merged', 'deprecated');

CREATE VIEW IF NOT EXISTS current_relationships AS
SELECT r.*, e1.name AS from_name, e2.name AS to_name
FROM relationships r
JOIN entities e1 ON r.from_entity = e1.id
JOIN entities e2 ON r.to_entity = e2.id
WHERE r.valid_until IS NULL
   OR date('now') <= r.valid_until;

CREATE VIEW IF NOT EXISTS matters_with_deadlines AS
SELECT
    m.id AS matter_id,
    m.name AS matter_name,
    d.name AS deadline_name,
    json_extract(d.attributes, '$.date') AS deadline_date,
    json_extract(d.attributes, '$.description') AS deadline_description
FROM entities m
JOIN relationships r ON r.to_entity = m.id AND r.type = 'deadline_for'
JOIN entities d ON r.from_entity = d.id
WHERE m.type = 'legal_matter'
ORDER BY deadline_date;

CREATE VIEW IF NOT EXISTS assertion_summary AS
SELECT
    e.name,
    e.type,
    COUNT(*) AS total_assertions,
    SUM(CASE WHEN a.confidence = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
    SUM(CASE WHEN a.confidence = 'believed' THEN 1 ELSE 0 END) AS believed,
    SUM(CASE WHEN a.confidence = 'suspected' THEN 1 ELSE 0 END) AS suspected,
    SUM(CASE WHEN a.confidence = 'hypothesized' THEN 1 ELSE 0 END) AS hypothesized
FROM assertions a
JOIN entities e ON a.entity_id = e.id
GROUP BY e.id;

-- Rebuild indexes with v3_ prefix
CREATE INDEX IF NOT EXISTS idx_v3_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_v3_entities_status ON entities(status);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_entity ON assertions(entity_id);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_confidence ON assertions(confidence);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_confidence_score ON assertions(confidence_score);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_derivation ON assertions(derivation_type);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_review ON assertions(review_status);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_superseded ON assertions(superseded_by);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_valid_from ON assertions(valid_from);
CREATE INDEX IF NOT EXISTS idx_v3_assertions_chunk ON assertions(chunk_id);
CREATE INDEX IF NOT EXISTS idx_v3_chunks_source ON chunks(source_uri);
CREATE INDEX IF NOT EXISTS idx_v3_chunks_observer ON chunks(observer);
CREATE INDEX IF NOT EXISTS idx_v3_sf_mention ON surface_forms(mention);
CREATE INDEX IF NOT EXISTS idx_v3_sf_entity ON surface_forms(entity_id);
CREATE INDEX IF NOT EXISTS idx_v3_sf_context ON surface_forms(context_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_v3_sf_cache ON surface_forms(mention, context_hash);
CREATE INDEX IF NOT EXISTS idx_v3_rel_source ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_v3_rel_target ON relationships(to_entity);
CREATE INDEX IF NOT EXISTS idx_v3_rel_type ON relationships(type);
CREATE INDEX IF NOT EXISTS idx_v3_extruns_status ON extraction_runs(status);
CREATE INDEX IF NOT EXISTS idx_v3_extruns_source ON extraction_runs(source_uri);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (5, 'v3.0 clean-slate migration per cortex-spec-v2.1.md §12');
