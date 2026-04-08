-- Migration 004: Cortex v2.1 — assertion review tracking, relationship extensions

-- === ASSERTIONS: review workflow columns ===
ALTER TABLE assertions ADD COLUMN review_status TEXT DEFAULT 'committed'
    CHECK(review_status IN ('committed', 'flagged', 'staged', 'rejected'));
ALTER TABLE assertions ADD COLUMN reviewer TEXT;
ALTER TABLE assertions ADD COLUMN reviewed_at TEXT;

-- === RELATIONSHIPS: provenance and temporal columns ===
ALTER TABLE relationships ADD COLUMN strength REAL DEFAULT 1.0;
ALTER TABLE relationships ADD COLUMN evidence TEXT;
ALTER TABLE relationships ADD COLUMN chunk_id INTEGER REFERENCES chunks(id);
ALTER TABLE relationships ADD COLUMN valid_from TEXT;
ALTER TABLE relationships ADD COLUMN valid_until TEXT;

-- === INDEXES ===
CREATE INDEX IF NOT EXISTS idx_assertions_review_status
    ON assertions(review_status);
CREATE INDEX IF NOT EXISTS idx_relationships_valid
    ON relationships(valid_from, valid_until);
