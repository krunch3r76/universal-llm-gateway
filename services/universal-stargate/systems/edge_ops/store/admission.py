"""Row mapping, idempotent admit, and record reads on the edge-op store.

``admit_sync`` is insert-or-return: a caller that re-admits with the same
idempotency key inside the dedupe window gets the same ``op_id`` back and no
second row. The dedupe window is the op deadline plus a grace, so it always
covers at least the op's own lifetime.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from ..model import OpRecord, Redelivery

_RECORD_COLUMNS = (
    "op_id, kind, op, args_json, redelivery, idempotency_key, state, "
    "admitted_at_epoch, deadline_epoch, leased_by, lease_id, "
    "lease_expires_epoch, started_at_epoch, result_json, updated_at_epoch"
)


def row_to_record(row: sqlite3.Row) -> OpRecord:
    """Map one ``edge_ops`` row onto the folded record shape."""
    result_json = row["result_json"]
    return OpRecord(
        op_id=row["op_id"],
        kind=row["kind"],
        op=row["op"],
        args=json.loads(row["args_json"]),
        redelivery=row["redelivery"],
        idempotency_key=row["idempotency_key"],
        state=row["state"],
        admitted_at_epoch=row["admitted_at_epoch"],
        deadline_epoch=row["deadline_epoch"],
        updated_at_epoch=row["updated_at_epoch"],
        leased_by=row["leased_by"],
        lease_id=row["lease_id"],
        lease_expires_epoch=row["lease_expires_epoch"],
        started_at_epoch=row["started_at_epoch"],
        result=None if result_json is None else json.loads(result_json),
    )


def fetch_sync(connection: sqlite3.Connection, op_id: str) -> OpRecord | None:
    """Return the folded record for *op_id*, or ``None`` when it is unknown."""
    row = connection.execute(
        f"SELECT {_RECORD_COLUMNS} FROM edge_ops WHERE op_id = ?",
        (op_id,),
    ).fetchone()
    return None if row is None else row_to_record(row)


def admit_sync(
    connection: sqlite3.Connection,
    *,
    kind: str,
    op: str,
    args: dict[str, Any],
    redelivery: Redelivery,
    idempotency_key: str,
    now: float,
    deadline_epoch: float,
    dedupe_until_epoch: float,
) -> tuple[OpRecord, bool]:
    """Insert a ``queued`` op, or return the live row for the same caller key.

    Returns ``(record, created)``. ``created`` is False when the idempotency
    key already resolves inside its dedupe window — the substrate hands back
    the original op rather than queueing a duplicate.
    """
    existing = connection.execute(
        f"SELECT {_RECORD_COLUMNS} FROM edge_ops "
        "WHERE idempotency_key = ? AND dedupe_until_epoch > ? "
        "ORDER BY admitted_at_epoch DESC LIMIT 1",
        (idempotency_key, now),
    ).fetchone()
    if existing is not None:
        return row_to_record(existing), False

    op_id = f"edgeop-{uuid.uuid4().hex}"
    connection.execute(
        "INSERT INTO edge_ops ("
        "op_id, kind, op, args_json, redelivery, idempotency_key, state, "
        "admitted_at_epoch, deadline_epoch, dedupe_until_epoch, updated_at_epoch"
        ") VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
        (
            op_id,
            kind,
            op,
            json.dumps(args, sort_keys=True),
            redelivery,
            idempotency_key,
            now,
            deadline_epoch,
            dedupe_until_epoch,
            now,
        ),
    )
    connection.execute(
        "INSERT INTO edge_op_transitions (op_id, state, reason, at_epoch) "
        "VALUES (?, 'queued', 'admitted', ?)",
        (op_id, now),
    )
    connection.commit()
    record = fetch_sync(connection, op_id)
    if record is None:  # pragma: no cover - defensive
        raise RuntimeError(f"admitted op {op_id} not readable")
    return record, True
