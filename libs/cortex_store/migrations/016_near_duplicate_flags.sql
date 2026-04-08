-- Near-duplicate flags table for assertion dedup observability (Phase 3).
-- Records SequenceMatcher-detected near-duplicates as graph metadata.
CREATE TABLE IF NOT EXISTS near_duplicate_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assertion_id INTEGER NOT NULL REFERENCES assertions(id),
    duplicate_of INTEGER NOT NULL REFERENCES assertions(id),
    score REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_near_dup_assertion ON near_duplicate_flags(assertion_id);
CREATE INDEX IF NOT EXISTS idx_near_dup_of ON near_duplicate_flags(duplicate_of);
