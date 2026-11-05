"""Post-commit advisory delivery from the outbox (never used by recovery)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

logger = get_logger("cortex_store.concern_aggregate.advisory_outbox")


class AdvisoryEmitError(RuntimeError):
    """Raised when advisory sink delivery fails (commit already durable)."""


def enqueue_outbox(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    seq: int,
    record_ref: str,
    payload: dict[str, Any],
) -> None:
    """Insert a pending advisory row in the same transaction as the journal commit."""
    conn.execute(
        "INSERT INTO concern_advisory_outbox "
        "(concern_id, seq, record_ref, payload) VALUES (?, ?, ?, ?)",
        (concern_id, seq, record_ref, json.dumps(payload, sort_keys=True)),
    )


def emit_pending(
    conn: sqlite3.Connection,
    *,
    concern_id: str | None = None,
    sink: Callable[[str, int, str, dict[str, Any]], None] | None = None,
    fail_on_seq: int | None = None,
) -> list[int]:
    """Deliver pending outbox rows post-commit; idempotent per seq.

    *sink* defaults to writing ``concern_advisory_delivered``. When
    *fail_on_seq* is set, simulates sink failure for that seq (A14).
    """
    where = "WHERE emitted_at IS NULL"
    params: list[Any] = []
    if concern_id is not None:
        where += " AND concern_id = ?"
        params.append(concern_id)
    rows = conn.execute(
        f"SELECT concern_id, seq, record_ref, payload FROM concern_advisory_outbox {where} "
        "ORDER BY concern_id, seq",
        params,
    ).fetchall()
    emitted: list[int] = []
    for cid, seq, record_ref, payload in rows:
        if fail_on_seq is not None and int(seq) == fail_on_seq:
            raise AdvisoryEmitError(f"simulated sink failure for seq {seq}")
        body = json.loads(payload)
        if sink is not None:
            sink(cid, int(seq), record_ref, body)
        else:
            conn.execute(
                "INSERT INTO concern_advisory_delivered "
                "(concern_id, seq, event_kind, payload) VALUES (?, ?, ?, ?)",
                (cid, seq, body.get("event_kind", "transition"), payload),
            )
        conn.execute(
            "UPDATE concern_advisory_outbox SET emitted_at = datetime('now') "
            "WHERE concern_id = ? AND seq = ? AND emitted_at IS NULL",
            (cid, seq),
        )
        emitted.append(int(seq))
    if emitted:
        conn.commit()
        logger.info("advisory_outbox: emitted %s rows for concern=%s", len(emitted), concern_id)
    return emitted


def count_pending(conn: sqlite3.Connection, concern_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM concern_advisory_outbox "
        "WHERE concern_id = ? AND emitted_at IS NULL",
        (concern_id,),
    ).fetchone()
    return int(row[0]) if row else 0
