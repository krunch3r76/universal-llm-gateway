"""Add act receipt verification columns to triggers table."""

from __future__ import annotations

import sqlite3

MIGRATION_ID = "003_act_receipt"

_DDL = """
ALTER TABLE triggers ADD COLUMN act_status TEXT NOT NULL DEFAULT 'n/a'
    CHECK (act_status IN ('n/a','pending','claimed','verified','unverified'));
ALTER TABLE triggers ADD COLUMN act_evidence_uri TEXT;
ALTER TABLE triggers ADD COLUMN act_error TEXT;
ALTER TABLE triggers ADD COLUMN require_act_receipt INTEGER;
"""


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)
