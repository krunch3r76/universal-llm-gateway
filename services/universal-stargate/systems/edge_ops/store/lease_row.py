"""Row reads and refusal helpers shared by lease write paths."""

from __future__ import annotations

import sqlite3

from ..model import CODE_LEASE_STALE, CODE_TERMINAL, EdgeOpRefusedError


def owned_row_sync(
    connection: sqlite3.Connection,
    op_id: str,
    edge_id: str,
    lease_id: str,
) -> sqlite3.Row | None:
    """Return the op row when *edge_id* holds *lease_id*, else refuse or ``None``."""
    row = connection.execute(
        "SELECT state, kind, redelivery, lease_id FROM edge_ops "
        "WHERE op_id = ? AND leased_by = ?",
        (op_id, edge_id),
    ).fetchone()
    if row is None:
        return None
    if row["lease_id"] != lease_id:
        raise EdgeOpRefusedError(
            409,
            CODE_LEASE_STALE,
            f"Edge op {op_id!r}: lease {lease_id!r} is stale for edge {edge_id!r}.",
        )
    return row


def row_after_sync(
    connection: sqlite3.Connection,
    op_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT state, kind, redelivery, lease_id FROM edge_ops WHERE op_id = ?",
        (op_id,),
    ).fetchone()


def refuse_started_terminal_sync(
    connection: sqlite3.Connection,
    op_id: str,
    state: str,
) -> None:
    raise EdgeOpRefusedError(
        409,
        CODE_TERMINAL,
        f"Edge op {op_id!r} is {state!r}: a terminal op cannot be "
        "started, and its handler must not run.",
    )
