"""Terminal ``expired`` write after a cancel directive is consumed."""

from __future__ import annotations

import sqlite3

from ..model import REASON_DEADLINE, TERMINAL_STATES
from .lease_row import owned_row_sync, row_after_sync
from .schema import record_transition_sync


def expire_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    now: float,
) -> tuple[str, str] | None:
    """Fold a started op ``expired`` after the edge consumed a cancel directive."""
    row = owned_row_sync(connection, op_id, edge_id, lease_id)
    if row is None:
        return None
    state = row["state"]
    kind = row["kind"]
    if state in TERMINAL_STATES:
        return state, kind
    if state != "started":
        fresh = row_after_sync(connection, op_id)
        if fresh is not None and fresh["state"] in TERMINAL_STATES:
            return fresh["state"], fresh["kind"]
        return None
    updated = connection.execute(
        "UPDATE edge_ops SET state = 'expired', lease_expires_epoch = NULL, "
        "updated_at_epoch = ? WHERE op_id = ? AND lease_id = ? AND state = 'started'",
        (now, op_id, lease_id),
    ).rowcount
    if not updated:
        fresh = row_after_sync(connection, op_id)
        if fresh is not None and fresh["state"] in TERMINAL_STATES:
            return fresh["state"], fresh["kind"]
        return None
    record_transition_sync(
        connection,
        op_id=op_id,
        state="expired",
        reason=REASON_DEADLINE,
        at_epoch=now,
    )
    connection.commit()
    return "expired", kind
