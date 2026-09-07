"""Migration 065: register derived_from structural relationship type for views."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.065")


def migrate(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        (
            "derived_from",
            "Derived-view provenance: document is a rendered projection of the "
            "target root entity (PROV wasDerivedFrom at document scale)",
        ),
    )
    logger.info(
        "Migration 065: derived_from relationship type (inserted=%d)",
        cur.rowcount,
    )
