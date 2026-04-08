"""Migration 026: Ephemeral entities — retention policies, access-based TTL, edge types.

Adds retention_policy, retention_ttl_days, last_accessed_at to entities.
Registers 'continues' (directional) and 'relates_to' (non-directional) edge types.
Creates trigger to materialize last_accessed_at from entity_access_log.
"""

from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        ALTER TABLE entities ADD COLUMN retention_policy TEXT NOT NULL DEFAULT 'permanent';
        ALTER TABLE entities ADD COLUMN retention_ttl_days INTEGER DEFAULT NULL;
        ALTER TABLE entities ADD COLUMN last_accessed_at TEXT DEFAULT NULL;
    """)

    conn.executescript("""
        INSERT OR IGNORE INTO session_edge_types (type, description, directional)
        VALUES
            ('continues', 'Temporal continuation — this session resumes a prior session', TRUE),
            ('relates_to', 'General thematic or topical connection', FALSE);
    """)

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS trg_update_last_accessed
        AFTER INSERT ON entity_access_log
        BEGIN
            UPDATE entities
            SET last_accessed_at = NEW.created_at
            WHERE id = NEW.entity_id;
        END;
    """)

    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, description) "
        "VALUES (26, '026_ephemeral_entities: retention policies, access TTL, continues+relates_to edge types')"
    )
    conn.commit()
