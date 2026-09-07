"""Migration 077: verbatim fingerprint columns on ``session_journals`` (R3).

Adds ``verbatim_sha256`` and ``verbatim_bytes`` so seal/fill/render can
partition the verbatim layer without scanning for ``## Session Summary``.
"""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("cortex-api.migration.077")

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("verbatim_sha256", "TEXT"),
    ("verbatim_bytes", "INTEGER"),
)


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE ADD COLUMN for verbatim fingerprint fields."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(session_journals)")}
    for name, col_type in _COLUMNS:
        if name in existing:
            logger.info(
                "migration 077: session_journals.%s already present; skip", name
            )
            continue
        conn.execute(
            f"ALTER TABLE session_journals ADD COLUMN {name} {col_type}"
        )
        logger.info("migration 077: session_journals.%s added", name)
