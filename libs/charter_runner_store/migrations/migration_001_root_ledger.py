"""Migration 001: root_ledger, consult_queue, ledger_meta (schema_version=1)."""

from __future__ import annotations

import sqlite3

from universal_logging import get_logger

logger = get_logger("charter_runner_store.migration.001")

MIGRATION_ID = "migration_001_root_ledger"


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS root_ledger (
          root_id            TEXT PRIMARY KEY,
          schema_version     INTEGER NOT NULL DEFAULT 1,
          status             TEXT NOT NULL,
          pickup_gid         TEXT,
          pickup_lane        TEXT,
          pickup_executor    TEXT,
          wip_window_id      TEXT,
          revise_count       INTEGER NOT NULL DEFAULT 0,
          consult_role       TEXT,
          consult_attempts   INTEGER NOT NULL DEFAULT 0,
          consult_next_retry REAL,
          consult_poll_from  TEXT,
          harvest_deadline   REAL,
          attendance         TEXT NOT NULL,
          scoreboard_uri     TEXT NOT NULL,
          last_window_id     TEXT,
          last_transition    TEXT,
          last_error         TEXT,
          env_facts_json     TEXT,
          updated_at         REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS consult_queue (
          id              INTEGER PRIMARY KEY,
          root_id         TEXT NOT NULL,
          gid             TEXT NOT NULL,
          consult_role    TEXT NOT NULL,
          corpus_sha      TEXT,
          attempts        INTEGER NOT NULL DEFAULT 0,
          next_retry      REAL,
          status          TEXT NOT NULL,
          created_at      REAL NOT NULL,
          updated_at      REAL NOT NULL,
          UNIQUE(root_id, gid, consult_role)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger_meta (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO ledger_meta (key, value)
        VALUES ('schema_version', '1')
        """
    )
    logger.info("migration 001: root_ledger tables ready")
