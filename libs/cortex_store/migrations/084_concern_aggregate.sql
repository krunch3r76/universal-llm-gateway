-- Migration 084: Phase-1 typed concern aggregate (journal + views + checkpoint + outbox).

CREATE TABLE IF NOT EXISTS concern_seq_alloc (
    concern_id TEXT NOT NULL PRIMARY KEY,
    next_seq INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS concern_journal (
    concern_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    record_ref TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    authorized_by TEXT NOT NULL,
    fencing_generation INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    fold_version INTEGER NOT NULL DEFAULT 1,
    schema_version INTEGER NOT NULL DEFAULT 1,
    committed_at TEXT,
    PRIMARY KEY (concern_id, seq),
    UNIQUE (record_ref)
);

CREATE INDEX IF NOT EXISTS idx_concern_journal_concern_committed
    ON concern_journal (concern_id, committed_at);

CREATE TABLE IF NOT EXISTS concern_head (
    concern_id TEXT NOT NULL PRIMARY KEY,
    revision INTEGER NOT NULL DEFAULT 0,
    record_ref TEXT,
    fencing_generation INTEGER NOT NULL DEFAULT 0,
    fold_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS concern_controls (
    concern_id TEXT NOT NULL,
    control_key TEXT NOT NULL,
    hold_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (concern_id, control_key)
);

CREATE TABLE IF NOT EXISTS concern_intents (
    concern_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    intent_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (concern_id, intent_id)
);

CREATE TABLE IF NOT EXISTS concern_cursor (
    concern_id TEXT NOT NULL PRIMARY KEY,
    through_seq INTEGER NOT NULL DEFAULT 0,
    cursor_token TEXT NOT NULL DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS concern_checkpoint (
    concern_id TEXT NOT NULL,
    through_seq INTEGER NOT NULL,
    fold_version INTEGER NOT NULL,
    folded_state TEXT NOT NULL,
    state_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (concern_id, through_seq)
);

CREATE TABLE IF NOT EXISTS concern_advisory_outbox (
    concern_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    record_ref TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    emitted_at TEXT,
    PRIMARY KEY (concern_id, seq),
    UNIQUE (record_ref)
);

-- Delivered advisories (sink); never read by recovery or admission.
CREATE TABLE IF NOT EXISTS concern_advisory_delivered (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concern_id TEXT NOT NULL,
    seq INTEGER,
    event_kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    delivered_at TEXT NOT NULL DEFAULT (datetime('now'))
);
