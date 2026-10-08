"""Restart-intent status transitions. The arm ``reason`` column is never written here."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def decode_transitions(raw: Any) -> list[dict[str, str]]:
    """Legacy NULL and non-lists read as an empty history."""
    if raw is None or not str(raw).strip():
        return []
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "status": str(item.get("status") or ""),
                "reason": str(item.get("reason") or ""),
                "at": str(item.get("at") or ""),
            }
        )
    return out


def transition_fields(
    row: sqlite3.Row,
) -> tuple[str | None, str | None, list[dict[str, str]]]:
    keys = set(row.keys())
    reason = row["status_reason"] if "status_reason" in keys else None
    changed = row["status_changed_at"] if "status_changed_at" in keys else None
    raw = row["transitions"] if "transitions" in keys else None
    return reason, changed, decode_transitions(raw)


def append_status_transition(
    conn: sqlite3.Connection,
    intent_id: str,
    *,
    status: str,
    reason: str,
    at: str | None = None,
) -> int:
    """Read-modify-write one history entry. Caller holds ``BEGIN IMMEDIATE``.

    Sets ``status``, ``status_reason``, and ``status_changed_at``. Does not
    touch the arm ``reason`` column.
    """
    row = conn.execute(
        "SELECT transitions FROM restart_intents WHERE intent_id=?",
        (intent_id,),
    ).fetchone()
    if row is None:
        return 0
    history = decode_transitions(row["transitions"])
    stamp = at or _now()
    history.append({"status": status, "reason": reason, "at": stamp})
    blob = json.dumps(history, separators=(",", ":"))
    cursor = conn.execute(
        """
        UPDATE restart_intents
        SET status=?, status_reason=?, status_changed_at=?, transitions=?, updated_at=?
        WHERE intent_id=?
        """,
        (status, reason, stamp, blob, stamp, intent_id),
    )
    return int(cursor.rowcount)


def write_status_reason(
    conn: sqlite3.Connection, intent_id: str, *, status_reason: str
) -> None:
    """Waiting note. Does not append a transition or change status."""
    conn.execute(
        "UPDATE restart_intents SET status_reason=? WHERE intent_id=?",
        (status_reason, intent_id),
    )
