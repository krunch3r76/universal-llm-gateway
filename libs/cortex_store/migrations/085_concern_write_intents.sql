-- Migration 085: Phase-2 position CAS — write intents + journal rebuild columns.

CREATE TABLE IF NOT EXISTS concern_write_intents (
    concern_id TEXT NOT NULL,
    write_intent_id TEXT NOT NULL,
    material_fingerprint TEXT NOT NULL,
    result_record_ref TEXT NOT NULL,
    result_revision INTEGER NOT NULL,
    result_seq INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (concern_id, write_intent_id)
);

CREATE INDEX IF NOT EXISTS idx_concern_write_intents_concern
    ON concern_write_intents (concern_id);

ALTER TABLE concern_journal ADD COLUMN write_intent_id TEXT;
ALTER TABLE concern_journal ADD COLUMN material_fingerprint TEXT;
