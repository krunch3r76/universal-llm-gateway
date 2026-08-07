"""migration_006: Backfill A′ lifecycle enrollment for mission private lanes.

``agent_bus.request`` now sets ``bus_lifecycle_state='active'`` at new-slug
birth. Lanes minted before that enroll left the column NULL, so the quiet
sweep candidacy filter (``IN ('admitted','active')``) never saw them.

Backfill only ``lane:cursor-auto`` threads that are still NULL — that tag is
the operator-proxy / mission private-lane marker. Do not enroll arbitrary
NULL threads (unenrolled remains unenrolled).
"""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_006"


def run(conn: sqlite3.Connection) -> None:
    """Set ``active`` on unenrolled ``lane:cursor-auto`` threads."""
    conn.execute(
        """
        UPDATE threads
        SET bus_lifecycle_state = 'active',
            updated_at = datetime('now')
        WHERE bus_lifecycle_state IS NULL
          AND id IN (
              SELECT thread_id FROM thread_tags
              WHERE tag = 'lane:cursor-auto'
          )
        """
    )
