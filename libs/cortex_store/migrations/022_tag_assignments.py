"""Migration 022: Tag assignments — Kumiho mutable tag pointers.

Adds the tag_assignments table: named mutable references that can point at
any assertion within an entity.  Enables point-in-time belief reconstruction,
named states (approved, initial, disputed), and formal belief-base definition.

Kumiho Definition 4.2: tags are independent mutable references within an item.
Entity = Kumiho item, assertion = Kumiho revision.

Origin: Agent bus thread 450, Phase A2 of cortex-v3-kumiho-complete.md
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("cortex-api.migration.022")


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tag_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            assertion_id INTEGER NOT NULL,
            assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
            assigned_by TEXT NOT NULL,
            UNIQUE(tag_name, entity_id),
            FOREIGN KEY (entity_id) REFERENCES entities(id),
            FOREIGN KEY (assertion_id) REFERENCES assertions(id)
        )
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tag_entity ON tag_assignments(entity_id)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tag_name ON tag_assignments(tag_name)")

    logger.info("Migration 022 (tag_assignments — Kumiho tag pointers) complete")
