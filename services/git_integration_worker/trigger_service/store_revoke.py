"""Revoke recurring triggers — stop future fires without aborting in-flight episodes."""

from __future__ import annotations

import sqlite3

from .db import now_iso
from .models import (
    STATUS_CANCELLED,
    STATUS_SCHEDULED,
    TriggerRow,
    TriggerStoreError,
    require_status,
    row_from_db,
)


def revoke_trigger(conn: sqlite3.Connection, trigger_id: str) -> TriggerRow:
    """Clear ``recur_every_s`` and cancel when still scheduled.

    Fired or firing rows keep their current episode but will not re-arm after
    terminal reconcile. Idempotent when recurrence is already cleared.
    """
    row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    if row is None:
        raise TriggerStoreError(f"unknown trigger id: {trigger_id}")
    current = row_from_db(row)
    if current.recur_every_s is None and current.status != STATUS_SCHEDULED:
        return current
    cancelled_at = now_iso()
    if current.status == STATUS_SCHEDULED:
        conn.execute(
            """
            UPDATE triggers
            SET recur_every_s = NULL, status = ?, cancelled_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                require_status(STATUS_CANCELLED),
                cancelled_at,
                trigger_id,
                STATUS_SCHEDULED,
            ),
        )
    else:
        conn.execute(
            "UPDATE triggers SET recur_every_s = NULL WHERE id = ?",
            (trigger_id,),
        )
    conn.commit()
    updated = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    assert updated is not None
    return row_from_db(updated)
