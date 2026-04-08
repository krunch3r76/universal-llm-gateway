-- Migration 018: Working memory enhancements
-- Adds supersession_scope for multi-observer attribution
-- Adds index on seeded_by (column exists from migration 009 but was unindexed)
-- Adds investigate edge type for "suspected connection, unestablished"

ALTER TABLE assertions ADD COLUMN supersession_scope TEXT DEFAULT 'local';

CREATE INDEX IF NOT EXISTS idx_assertions_seeded_by ON assertions(seeded_by);
CREATE INDEX IF NOT EXISTS idx_assertions_entity_seeded ON assertions(entity_id, seeded_by);

INSERT OR IGNORE INTO session_edge_types (type, description, directional) VALUES
    ('investigates', 'Suspected connection requiring further analysis', FALSE);
