"""Migration 006: drain consult_queue orphans under already-CLOSED roots (a:27395)."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.006")

MIGRATION_ID = "migration_006_consult_orphan_drain"


def migrate(conn: sqlite3.Connection) -> None:
    from scripts.model_manager.ui.controller.charter_runner.consult_drain import (
        drain_orphan_consults_under_closed_roots,
    )

    drained = drain_orphan_consults_under_closed_roots(
        conn,
        reason="migration_006_orphan_cleanup",
    )
    conn.commit()
    logger.info(
        "migration 006: drained %d orphan consult_queue row(s)",
        len(drained),
    )
