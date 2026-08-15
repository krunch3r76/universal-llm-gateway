"""Tests for restart-intent schema migration."""

from __future__ import annotations

import sqlite3

from scripts.model_manager.ui.controller.restart_intent_migrate import (
    apply_restart_intent_schema,
)
from scripts.model_manager.ui.controller.restart_intent_states import (
    STATUS_VERIFYING_ACTIVATION,
)


def test_migration_allows_verifying_activation(tmp_path) -> None:
    """Schema migration preserves verifying_activation status rows across re-apply."""
    db = tmp_path / "restart-intents.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE restart_intents (
            intent_id TEXT PRIMARY KEY,
            service TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT 'restart',
            status TEXT NOT NULL CHECK (status IN (
                'pending_drain','drained_restarting','completed',
                'failed','timeout','force_requested','cancelled')),
            drain_epoch INTEGER,
            worker_id TEXT,
            worker_started_at TEXT,
            deadline_at TEXT,
            last_seen_event_seq INTEGER NOT NULL DEFAULT 0,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    apply_restart_intent_schema(conn)
    conn.execute(
        "INSERT INTO restart_intents (intent_id, service, status, created_at, updated_at) "
        "VALUES ('i1', 'git_integration_worker', ?, 't', 't')",
        (STATUS_VERIFYING_ACTIVATION,),
    )
    conn.commit()
    apply_restart_intent_schema(conn)
    row = conn.execute(
        "SELECT status FROM restart_intents WHERE intent_id='i1'"
    ).fetchone()
    assert row[0] == STATUS_VERIFYING_ACTIVATION
