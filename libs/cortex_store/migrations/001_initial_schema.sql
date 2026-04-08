-- Migration 001: Initial Cortex schema
-- Applied: 2026-03-15
-- Description: Core tables for entities, assertions, relationships, sessions

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'confirmed',
    notes TEXT,
    aliases TEXT,
    attributes TEXT,
    source_uri TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assertions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    claim TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'believed',
    confidence_score REAL,
    evidence TEXT,
    evidence_uris TEXT,
    derivation_type TEXT NOT NULL DEFAULT 'inference',
    reasoning_summary TEXT,
    observed_at DATETIME,
    valid_from DATETIME,
    valid_until DATETIME,
    superseded_by INTEGER REFERENCES assertions(id),
    review_status TEXT DEFAULT 'committed',
    reviewer TEXT,
    reviewed_at DATETIME,
    review_notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationship_types (
    type TEXT PRIMARY KEY,
    description TEXT,
    inverse TEXT REFERENCES relationship_types(type),
    is_transitive BOOLEAN DEFAULT FALSE,
    is_symmetric BOOLEAN DEFAULT FALSE,
    from_type TEXT,
    to_type TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL REFERENCES relationship_types(type),
    from_entity TEXT NOT NULL REFERENCES entities(id),
    to_entity TEXT NOT NULL REFERENCES entities(id),
    role TEXT,
    strength REAL DEFAULT 1.0,
    evidence TEXT,
    valid_from DATETIME,
    valid_until DATETIME,
    source_uri TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    active BOOLEAN NOT NULL DEFAULT 1,
    superseded_by INTEGER REFERENCES relationships(id)
);

CREATE TABLE IF NOT EXISTS session_journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    domains TEXT,
    decisions TEXT,
    open_items TEXT,
    file_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_assertions_entity ON assertions(entity_id);
CREATE INDEX IF NOT EXISTS idx_assertions_confidence ON assertions(confidence);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(from_entity);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(to_entity);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'Initial Cortex schema');
