"""migration_014: threads.idempotency_key for retry-safe create_thread (a:36915)."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "migration_014"


def run(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
    if "idempotency_key" not in columns:
        conn.execute("ALTER TABLE threads ADD COLUMN idempotency_key TEXT")
    # Partial unique index: NULL keys (every pre-existing row and every keyless
    # create) never collide; a keyed retry that races the first insert hits
    # IntegrityError and is resolved to the existing row by create_thread.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_threads_idempotency_key "
        "ON threads(idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
