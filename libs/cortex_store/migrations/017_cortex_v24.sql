-- Cortex v2.4 — Commitment tracking + quality enforcement
-- Adds resolution_status, fulfillment_assertion_id, quality_score to assertions.
-- Adds 'commitment' derivation_type support (validation at API layer).
-- Adds new session edge types for commitment tracking.

-- === ASSERTIONS EXTENSIONS ===
ALTER TABLE assertions ADD COLUMN resolution_status TEXT
    CHECK(resolution_status IN ('pending', 'fulfilled', 'breached', 'unknown'));

ALTER TABLE assertions ADD COLUMN fulfillment_assertion_id INTEGER
    REFERENCES assertions(id);

ALTER TABLE assertions ADD COLUMN quality_score REAL;

-- === SESSION EDGE TYPES ===
INSERT OR IGNORE INTO session_edge_types (type, description, directional)
VALUES ('promises', 'Entity/assertion promises a future state', 1);

INSERT OR IGNORE INTO session_edge_types (type, description, directional)
VALUES ('expects', 'Entity/assertion expects a future state', 1);

INSERT OR IGNORE INTO session_edge_types (type, description, directional)
VALUES ('leads_to', 'Causal forward link for open threads', 1);
