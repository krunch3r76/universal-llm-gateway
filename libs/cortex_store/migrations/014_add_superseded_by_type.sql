-- 014_add_superseded_by_type.sql
-- Add inverse of 'supersedes' for bidirectional chains.

INSERT OR IGNORE INTO session_edge_types (type, description, directional) VALUES
    ('superseded_by', 'Indicates that the from_node is superseded by the to_node (inverse of supersedes)', TRUE);
