"""Lease claim, renewal and terminal writes on the edge-op store.

Edge-initiated only: every function here runs because the edge called a
Stargate resource. Nothing in this module dials outward.

The edge identity is checked on every write, so one edge cannot renew, start,
finish or release another edge's op. A write against an op that already
reached a terminal state is a no-op that returns the stored state — the
substrate never retries, reopens or overwrites a completed op.

The one ordering rule enforced here is on the terminal write: a ``never`` op's
result is refused until its ``started`` event has landed.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ..model import (
    CODE_NOT_STARTED,
    TERMINAL_STATES,
    EdgeOpRefusedError,
    LeaseGrant,
    Outcome,
)
from .schema import record_transition_sync


def record_poll_sync(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    kinds: tuple[str, ...],
    now: float,
) -> None:
    """Stamp this edge's presence. ``admit`` reads it for ``edge_unavailable``."""
    connection.execute(
        "INSERT INTO edge_polls (edge_id, last_poll_epoch, kinds_json) "
        "VALUES (?, ?, ?) ON CONFLICT(edge_id) DO UPDATE SET "
        "last_poll_epoch = excluded.last_poll_epoch, "
        "kinds_json = excluded.kinds_json",
        (edge_id, now, json.dumps(list(kinds))),
    )
    connection.commit()


def edge_seen_since_sync(connection: sqlite3.Connection, since: float) -> bool:
    """True when any edge has polled at or after *since*."""
    row = connection.execute(
        "SELECT 1 FROM edge_polls WHERE last_poll_epoch >= ? LIMIT 1",
        (since,),
    ).fetchone()
    return row is not None


def claim_lease_sync(
    connection: sqlite3.Connection,
    *,
    edge_id: str,
    kinds: tuple[str, ...],
    now: float,
    lease_ttl_s: float,
) -> LeaseGrant | None:
    """Claim the oldest queued op whose kind this edge serves, or ``None``.

    The claim is a guarded UPDATE on the selected row, so two edges polling
    the same kind at once cannot both win it.
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


def _owned_row_sync(
    connection: sqlite3.Connection,
    op_id: str,
    edge_id: str,
) -> sqlite3.Row | None:
    """Return the op row when *edge_id* currently holds its lease.

    Carries ``redelivery`` alongside ``state`` and ``kind``, because the
    terminal write has to know whether this op's class permits a result
    before ``started``.
    """
    return connection.execute(
        "SELECT state, kind, redelivery FROM edge_ops "
        "WHERE op_id = ? AND leased_by = ?",
        (op_id, edge_id),
    ).fetchone()


def renew_and_start_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: float,
    lease_ttl_s: float,
    started_event_type: str,
) -> tuple[str, int, str] | None:
    """Append one edge event, renew the lease, return ``(state, seq, kind)``.

    ``None`` means this edge does not hold the op's lease. An event whose type
    is *started_event_type* also moves ``leased`` to ``started``; the 2xx this
    produces is what a ``never`` handler waits for before it runs. Posting
    ``started`` twice renews without changing state.
    """
    row = _owned_row_sync(connection, op_id, edge_id)
    if row is None:
        return None
    state = row["state"]
    kind = row["kind"]
    if state in TERMINAL_STATES:
        seq = _append_event_sync(connection, op_id, event_type, payload, now)
        return state, seq, kind

    if event_type == started_event_type and state == "leased":
        state = "started"
        connection.execute(
            "UPDATE edge_ops SET state = 'started', started_at_epoch = ?, "
            "lease_expires_epoch = ?, updated_at_epoch = ? WHERE op_id = ?",
            (now, now + lease_ttl_s, now, op_id),
        )
        record_transition_sync(
            connection,
            op_id=op_id,
            state="started",
            reason=f"started_by:{edge_id}",
            at_epoch=now,
        )
    else:
        connection.execute(
            "UPDATE edge_ops SET lease_expires_epoch = ?, updated_at_epoch = ? "
            "WHERE op_id = ?",
            (now + lease_ttl_s, now, op_id),
        )
    seq = _append_event_sync(connection, op_id, event_type, payload, now)
    connection.commit()
    return state, seq, kind


def _append_event_sync(
    connection: sqlite3.Connection,
    op_id: str,
    event_type: str,
    payload: dict[str, Any],
    now: float,
) -> int:
    """Append an open-typed edge event and return its sequence number."""
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM edge_op_events "
        "WHERE op_id = ?",
        (op_id,),
    ).fetchone()
    seq = int(row["next_seq"])
    connection.execute(
        "INSERT INTO edge_op_events (op_id, seq, type, payload_json, at_epoch) "
        "VALUES (?, ?, ?, ?, ?)",
        (op_id, seq, event_type, json.dumps(payload, sort_keys=True), now),
    )
    connection.commit()
    return seq


def finish_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    edge_id: str,
    outcome: Outcome,
    result: Any,
    now: float,
) -> tuple[str, str] | None:
    """Store the handler result verbatim and set the terminal state.

    ``None`` means this edge does not hold the lease. An op already terminal
    keeps its stored state and result untouched.

    A ``never`` op must have posted ``started`` first. That 2xx is what its
    handler waits for, so a result arriving while the op is still ``leased``
    says the handler ran without the ack — and the lease-loss fold, seeing no
    ``started``, would have re-leased work that had already happened. Such a
    result is refused and nothing is written, so the op stays leased and the
    same edge can still post ``started`` and then its result. A ``safe`` op
    may result straight from ``leased``: a second delivery of a safe kind is
    harmless, so its handler never has to wait for the ack and may send no
    ``started`` at all.

    Raises:
        EdgeOpRefusedError: 409 ``edge_not_started`` when a ``never`` op that
            has not reached ``started`` posts a result.
    """
    row = _owned_row_sync(connection, op_id, edge_id)
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
    connection.execute(
        "UPDATE edge_ops SET state = ?, result_json = ?, "
        "lease_expires_epoch = NULL, updated_at_epoch = ? WHERE op_id = ?",
        (outcome, json.dumps(result), now, op_id),
    )
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
    now: float,
) -> tuple[str, str] | None:
    """Hand a leased op back to the queue, or mark a started one ``unknown``.

    ``None`` means this edge does not hold the lease. A started op cannot be
    re-run, so releasing it records ``unknown`` rather than requeueing.
    """
    row = _owned_row_sync(connection, op_id, edge_id)
    if row is None:
        return None
    state = row["state"]
    if state in TERMINAL_STATES:
        return state, row["kind"]
    if state == "started":
        connection.execute(
            "UPDATE edge_ops SET state = 'unknown', lease_expires_epoch = NULL, "
            "updated_at_epoch = ? WHERE op_id = ?",
            (now, op_id),
        )
        new_state = "unknown"
    else:
        connection.execute(
            "UPDATE edge_ops SET state = 'queued', leased_by = NULL, "
            "lease_id = NULL, lease_expires_epoch = NULL, updated_at_epoch = ? "
            "WHERE op_id = ?",
            (now, op_id),
        )
        new_state = "queued"
    record_transition_sync(
        connection,
        op_id=op_id,
        state=new_state,
        reason=f"released_by:{edge_id}",
        at_epoch=now,
    )
    connection.commit()
    return new_state, row["kind"]


def directives_after_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    after: int,
) -> list[dict[str, Any]]:
    """Return stored directives with ``seq`` greater than *after*.

    A1 writes no directives: the table exists so the edge's long-poll is a
    real read against the authority store rather than a hardcoded empty list,
    and so the cancel producer lands without a schema change.
    """
    rows = connection.execute(
        "SELECT seq, type, payload_json, at_epoch FROM edge_op_directives "
        "WHERE op_id = ? AND seq > ? ORDER BY seq ASC",
        (op_id, after),
    ).fetchall()
    return [
        {
            "seq": int(row["seq"]),
            "type": row["type"],
            "payload": json.loads(row["payload_json"]),
            "at_epoch": row["at_epoch"],
        }
        for row in rows
    ]
