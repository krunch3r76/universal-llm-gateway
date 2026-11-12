"""Lease claim, renewal and terminal writes on the edge-op store."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ..model import (
    CODE_NOT_STARTED,
    REASON_DEADLINE,
    TERMINAL_STATES,
    EdgeOpRefusedError,
    LeaseGrant,
    Outcome,
)
from .lease_events import append_event_sync
from .lease_row import owned_row_sync, refuse_started_terminal_sync, row_after_sync
from .schema import record_transition_sync

__all__ = [
    "claim_lease_sync",
    "expire_sync",
    "finish_sync",
    "release_sync",
    "renew_and_start_sync",
]


def claim_lease_sync(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    kinds: tuple[str, ...],
    now: float,
    lease_ttl_s: float,
) -> LeaseGrant | None:
    """Claim the oldest queued op whose kind this edge serves, or ``None``.

    Raises:
        EdgeOpRefusedError: not used; mismatched rows are skipped with ``None``.
    """
    if not kinds:
        return None
    placeholders = ", ".join("?" for _ in kinds)
    row = connection.execute(
        "SELECT op_id, kind, op, args_json, redelivery, deadline_epoch "
        "FROM edge_ops WHERE state = 'queued' "
        f"AND kind IN ({placeholders}) AND deadline_epoch > ? "
        "ORDER BY admitted_at_epoch ASC LIMIT 1",
        (*kinds, now),
    ).fetchone()
    if row is None:
        return None

    lease_id = f"lease-{uuid.uuid4().hex}"
    lease_expires_epoch = now + lease_ttl_s
    claimed = connection.execute(
        "UPDATE edge_ops SET state = 'leased', leased_by = ?, lease_id = ?, "
        "lease_expires_epoch = ?, updated_at_epoch = ? "
        "WHERE op_id = ? AND state = 'queued'",
        (edge_id, lease_id, lease_expires_epoch, now, row["op_id"]),
    ).rowcount
    if not claimed:
        return None
    record_transition_sync(
        connection,
        op_id=row["op_id"],
        state="leased",
        reason=f"leased_by:{edge_id}",
        at_epoch=now,
    )
    connection.commit()
    return LeaseGrant(
        op_id=row["op_id"],
        lease_id=lease_id,
        kind=row["kind"],
        op=row["op"],
        args=json.loads(row["args_json"]),
        redelivery=row["redelivery"],
        lease_ttl_s=lease_ttl_s,
        lease_expires_epoch=lease_expires_epoch,
        deadline_epoch=row["deadline_epoch"],
    )


def renew_and_start_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: float,
    lease_ttl_s: float,
    started_event_type: str,
) -> tuple[str, int, str] | None:
    """Append one edge event, renew the lease, return ``(state, seq, kind)``.

    Terminal ops still accept non-``started`` events: the returned ``state``
    names the terminal outcome so the edge learns the op closed without
    treating a 2xx as permission to run. Only ``started`` on a terminal op is
    refused (``edge_op_terminal``).

    Raises:
        EdgeOpRefusedError: ``edge_lease_stale`` when *lease_id* does not match,
            or ``edge_op_terminal`` when *event_type* is ``started`` on a
            terminal op.
    """
    row = owned_row_sync(connection, op_id, edge_id, lease_id)
    if row is None:
        return None
    state = row["state"]
    kind = row["kind"]
    if state in TERMINAL_STATES:
        if event_type == started_event_type:
            refuse_started_terminal_sync(connection, op_id, state)
        seq = append_event_sync(connection, op_id, event_type, payload, now)
        connection.commit()
        return state, seq, kind

    if event_type == started_event_type and state == "leased":
        updated = connection.execute(
            "UPDATE edge_ops SET state = 'started', started_at_epoch = ?, "
            "lease_expires_epoch = ?, updated_at_epoch = ? "
            "WHERE op_id = ? AND lease_id = ? AND state = 'leased'",
            (now, now + lease_ttl_s, now, op_id, lease_id),
        ).rowcount
        if not updated:
            fresh = row_after_sync(connection, op_id)
            if fresh is not None and fresh["state"] in TERMINAL_STATES:
                refuse_started_terminal_sync(connection, op_id, fresh["state"])
            return None
        state = "started"
        record_transition_sync(
            connection,
            op_id=op_id,
            state="started",
            reason=f"started_by:{edge_id}",
            at_epoch=now,
        )
    else:
        allowed = ("leased", "started") if state in {"leased", "started"} else (state,)
        updated = connection.execute(
            "UPDATE edge_ops SET lease_expires_epoch = ?, updated_at_epoch = ? "
            "WHERE op_id = ? AND lease_id = ? AND state IN "
            f"({', '.join('?' for _ in allowed)})",
            (now + lease_ttl_s, now, op_id, lease_id, *allowed),
        ).rowcount
        if not updated:
            fresh = row_after_sync(connection, op_id)
            if fresh is not None and fresh["state"] in TERMINAL_STATES:
                if event_type == started_event_type:
                    refuse_started_terminal_sync(connection, op_id, fresh["state"])
                return fresh["state"], 0, fresh["kind"]
            return None

    seq = append_event_sync(connection, op_id, event_type, payload, now)
    connection.commit()
    return state, seq, kind


def expire_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    now: float,
) -> tuple[str, str] | None:
    """Fold a started op ``expired`` after the edge consumed a cancel directive.

    Idempotent when the op is already terminal: returns that state without
    writing again.

    Raises:
        EdgeOpRefusedError: ``edge_lease_stale`` when *lease_id* does not match.
    """
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


def finish_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    outcome: Outcome,
    result: Any,
    now: float,
) -> tuple[str, str] | None:
    """Store the handler result verbatim and set the terminal state.

    Raises:
        EdgeOpRefusedError: ``edge_lease_stale`` or ``edge_not_started`` when
            the op is not in a finishable state for this lease holder.
    """
    row = owned_row_sync(connection, op_id, edge_id, lease_id)
    if row is None:
        return None
    state = row["state"]
    if state in TERMINAL_STATES:
        return state, row["kind"]
    if row["redelivery"] == "never" and state != "started":
        raise EdgeOpRefusedError(
            409,
            CODE_NOT_STARTED,
            f"Edge op {op_id!r} is {state!r}: a redelivery=never op must post "
            "a 'started' event before its result.",
        )
    updated = connection.execute(
        "UPDATE edge_ops SET state = ?, result_json = ?, "
        "lease_expires_epoch = NULL, updated_at_epoch = ? "
        "WHERE op_id = ? AND lease_id = ? AND state = ?",
        (outcome, json.dumps(result), now, op_id, lease_id, state),
    ).rowcount
    if not updated:
        fresh = row_after_sync(connection, op_id)
        if fresh is not None and fresh["state"] in TERMINAL_STATES:
            return fresh["state"], fresh["kind"]
        return None
    record_transition_sync(
        connection,
        op_id=op_id,
        state=outcome,
        reason=f"result_from:{edge_id}",
        at_epoch=now,
    )
    connection.commit()
    return outcome, row["kind"]


def release_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    lease_id: str,
    now: float,
) -> tuple[str, str] | None:
    """Hand a leased op back to the queue, or mark a started one ``unknown``.

    Raises:
        EdgeOpRefusedError: ``edge_lease_stale`` when *lease_id* does not match.
    """
    row = owned_row_sync(connection, op_id, edge_id, lease_id)
    if row is None:
        return None
    state = row["state"]
    if state in TERMINAL_STATES:
        return state, row["kind"]
    if state == "started":
        updated = connection.execute(
            "UPDATE edge_ops SET state = 'unknown', lease_expires_epoch = NULL, "
            "updated_at_epoch = ? WHERE op_id = ? AND lease_id = ? "
            "AND state = 'started'",
            (now, op_id, lease_id),
        ).rowcount
        new_state = "unknown" if updated else state
    else:
        updated = connection.execute(
            "UPDATE edge_ops SET state = 'queued', leased_by = NULL, "
            "lease_id = NULL, lease_expires_epoch = NULL, updated_at_epoch = ? "
            "WHERE op_id = ? AND lease_id = ? AND state = 'leased'",
            (now, op_id, lease_id),
        ).rowcount
        new_state = "queued" if updated else state
    if not updated:
        fresh = row_after_sync(connection, op_id)
        if fresh is not None and fresh["state"] in TERMINAL_STATES:
            return fresh["state"], fresh["kind"]
        return None
    record_transition_sync(
        connection,
        op_id=op_id,
        state=new_state,
        reason=f"released_by:{edge_id}",
        at_epoch=now,
    )
    connection.commit()
    return new_state, row["kind"]
