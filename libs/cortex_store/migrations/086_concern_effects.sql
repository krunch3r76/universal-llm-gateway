-- Migration 086: Phase-3 effect ledger — action intents, effect rows, per-work-key leases.

CREATE TABLE IF NOT EXISTS concern_action_intents (
    concern_id TEXT NOT NULL,
    work_key TEXT NOT NULL,
    action_slot TEXT NOT NULL,
    run_generation INTEGER NOT NULL,
    intent_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (concern_id, work_key, action_slot, run_generation)
);

CREATE INDEX IF NOT EXISTS idx_concern_action_intents_concern_work
    ON concern_action_intents (concern_id, work_key);

CREATE TABLE IF NOT EXISTS concern_effects (
    effect_key TEXT NOT NULL PRIMARY KEY,
    concern_id TEXT NOT NULL,
    work_key TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    material_fingerprint TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    state TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    attempts_json TEXT NOT NULL DEFAULT '[]',
    receipt_json TEXT,
    recovery_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_concern_effects_intent
    ON concern_effects (intent_id);

CREATE INDEX IF NOT EXISTS idx_concern_effects_concern_work
    ON concern_effects (concern_id, work_key);

CREATE TABLE IF NOT EXISTS concern_effect_leases (
    concern_id TEXT NOT NULL,
    work_key TEXT NOT NULL,
    holder TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    lease_expires_at TEXT NOT NULL,
    PRIMARY KEY (concern_id, work_key)
);
