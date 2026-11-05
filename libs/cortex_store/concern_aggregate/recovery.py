"""Rebuild materialized views from checkpoint + committed journal suffix."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from cortex_store.db import json_encode

from .checkpoint import latest_valid_checkpoint
from .journal import FOLD_VERSION
from .views import read_controls, read_cursor, read_head, snapshot_state


def _clear_views(conn: sqlite3.Connection, concern_id: str) -> None:
    conn.execute("DELETE FROM concern_head WHERE concern_id = ?", (concern_id,))
    conn.execute("DELETE FROM concern_controls WHERE concern_id = ?", (concern_id,))
    conn.execute("DELETE FROM concern_intents WHERE concern_id = ?", (concern_id,))
    conn.execute("DELETE FROM concern_cursor WHERE concern_id = ?", (concern_id,))


def _apply_state_snapshot(conn: sqlite3.Connection, concern_id: str, state: dict[str, Any]) -> None:
    head = state.get("head")
    if head:
        conn.execute(
            "INSERT INTO concern_head "
            "(concern_id, revision, record_ref, fencing_generation, fold_version) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                concern_id,
                head.get("revision", 0),
                head.get("record_ref"),
                head.get("fencing_generation", 0),
                head.get("fold_version", FOLD_VERSION),
            ),
        )
    for key, hold in (state.get("controls") or {}).items():
        conn.execute(
            "INSERT INTO concern_controls (concern_id, control_key, hold_json) VALUES (?, ?, ?)",
            (concern_id, key, json_encode(hold)),
        )
    for intent_id, body in (state.get("intents") or {}).items():
        conn.execute(
            "INSERT INTO concern_intents (concern_id, intent_id, record_ref, intent_json) "
            "VALUES (?, ?, ?, ?)",
            (
                concern_id,
                intent_id,
                body.get("record_ref"),
                json_encode(body.get("intent", {})),
            ),
        )
    through = int(state.get("through_seq", 0))
    conn.execute(
        "INSERT INTO concern_cursor (concern_id, through_seq, cursor_token) VALUES (?, ?, ?)",
        (concern_id, through, f"cur:{through}"),
    )


def _replay_suffix(
    conn: sqlite3.Connection,
    *,
    concern_id: str,
    after_seq: int,
) -> None:
    rows = conn.execute(
        "SELECT seq, kind, payload, record_ref, authorized_by, "
        "fencing_generation, revision FROM concern_journal "
        "WHERE concern_id = ? AND committed_at IS NOT NULL AND seq > ? "
        "ORDER BY seq",
        (concern_id, after_seq),
    ).fetchall()
    last_applied = after_seq
    for row in rows:
        seq = int(row[0])
        if seq != last_applied + 1:
            break
        last_applied = seq
        payload = json.loads(row[2]) if row[2] else {}
        kind = row[1]
        record_ref = row[3]
        # Re-apply view mutations without re-allocating seq or outbox.
        from .identities import (
            as_intent_id,
            as_record_ref,  # noqa: PLC0415
        )
        from .journal import _apply_transition_to_views  # noqa: PLC0415

        iid = None
        if kind == "action_intent" and payload.get("intent_id"):
            iid = as_intent_id(str(payload["intent_id"]))
        _apply_transition_to_views(
            conn,
            concern_id=concern_id,
            kind=kind,
            payload=payload,
            record_ref=as_record_ref(record_ref),
            intent_id=iid,
            revision=int(row[6]),
            fencing_generation=int(row[5]),
        )
    if rows:
        from .views import contiguous_through_seq

        through = contiguous_through_seq(conn, concern_id)
        conn.execute(
            "INSERT INTO concern_cursor (concern_id, through_seq, cursor_token) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(concern_id) DO UPDATE SET "
            "through_seq=excluded.through_seq, cursor_token=excluded.cursor_token",
            (concern_id, through, f"cur:{through}"),
        )


def rebuild_concern(
    conn: sqlite3.Connection,
    concern_id: str,
    *,
    fold_version: int = FOLD_VERSION,
) -> dict[str, Any]:
    """Rebuild views from last valid checkpoint plus committed suffix (no advisory reads)."""
    ckpt = latest_valid_checkpoint(conn, concern_id, expected_fold_version=fold_version)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _clear_views(conn, concern_id)
        start_seq = 0
        if ckpt is not None:
            _apply_state_snapshot(conn, concern_id, ckpt["state"])
            start_seq = int(ckpt["through_seq"])
        _replay_suffix(conn, concern_id=concern_id, after_seq=start_seq)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return snapshot_state(conn, concern_id)


def read_effective_state(conn: sqlite3.Connection, concern_id: str) -> dict[str, Any]:
    """Reader view: head + controls at current cursor (operational tables only)."""
    head = read_head(conn, concern_id)
    controls = read_controls(conn, concern_id)
    through, cursor = read_cursor(conn, concern_id)
    return {
        "head": head,
        "controls": controls,
        "through_seq": through,
        "cursor": str(cursor),
    }
