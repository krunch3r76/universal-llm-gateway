-- 013_session_edges.sql
-- Session edges: reasoning connections between entities across sessions.
-- Ratified: Web Claude (thread 336 turn 6), reviewed by Grok + API Claude.

CREATE TABLE IF NOT EXISTS session_edge_types (
    type        TEXT    PRIMARY KEY,
    description TEXT    NOT NULL,
    directional BOOLEAN NOT NULL DEFAULT TRUE
);

INSERT OR IGNORE INTO session_edge_types (type, description, directional) VALUES
    ('reasoned_about', 'General connection made through reasoning',                FALSE),
    ('caused_by',      'from_node was caused or triggered by to_node',             TRUE),
    ('contradicts',    'Tension or conflict between nodes',                         FALSE),
    ('extends',        'from_node builds on or develops to_node',                  TRUE),
    ('supersedes',     'from_node replaces or updates to_node',                    TRUE),
    ('analogous_to',   'Structural parallel between nodes',                        FALSE),
    ('evidence_for',   'from_node supports or provides evidence for to_node',      TRUE);

CREATE TABLE IF NOT EXISTS session_edges (
    id          INTEGER     PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    agent       TEXT        NOT NULL,
    from_node   TEXT        NOT NULL,
    to_node     TEXT        NOT NULL,
    edge_type   TEXT        NOT NULL    REFERENCES session_edge_types(type),
    strength    REAL        NOT NULL    DEFAULT 0.8
                            CHECK (strength >= 0.0 AND strength <= 1.0),
    edge_source TEXT        NOT NULL    DEFAULT 'explicit',
    context     TEXT,
    prompt      TEXT,
    seeded_by   TEXT,
    valid_until TIMESTAMP,
    metadata    TEXT,
    created_at  TIMESTAMP   NOT NULL    DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_se_from_type  ON session_edges(from_node, edge_type);
CREATE INDEX IF NOT EXISTS idx_se_to_type    ON session_edges(to_node, edge_type);
CREATE INDEX IF NOT EXISTS idx_se_session    ON session_edges(session_id);
CREATE INDEX IF NOT EXISTS idx_se_agent      ON session_edges(agent);
CREATE INDEX IF NOT EXISTS idx_se_strength   ON session_edges(strength);
CREATE INDEX IF NOT EXISTS idx_se_source     ON session_edges(edge_source);
CREATE INDEX IF NOT EXISTS idx_se_retired    ON session_edges(valid_until)
    WHERE valid_until IS NOT NULL;
