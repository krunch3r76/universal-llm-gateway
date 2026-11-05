"""Read paths for concern aggregate materialized views."""

from __future__ import annotations

import sqlite3
from typing import Any

from cortex_store.db import json_decode

from .identities import CursorToken, as_cursor_token


def read_head(conn: sqlite3.Connection, concern_id: str) -> dict[str, Any] | None:
    """Return head row for *concern_id*, or ``None`` when unset."""
    row = conn.execute(
        "SELECT revision, record_ref, fencing_generation, fold_version "
        "FROM concern_head WHERE concern_id = ?",
        (concern_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "revision": row[0],
        "record_ref": row[1],
        "fencing_generation": row[2],
        "fold_version": row[3],
    }


def read_controls(conn: sqlite3.Connection, concern_id: str) -> dict[str, Any]:
    """Effective hold map keyed by control_key."""
    rows = conn.execute(
        "SELECT control_key, hold_json FROM concern_controls WHERE concern_id = ? "
        "ORDER BY control_key",
        (concern_id,),
    ).fetchall()
    out: dict[str, Any] = {}
    for key, hold_json in rows:
        out[str(key)] = json_decode(hold_json, fallback={})
    return out


def read_intents(conn: sqlite3.Connection, concern_id: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT intent_id, record_ref, intent_json FROM concern_intents "
        "WHERE concern_id = ? ORDER BY intent_id",
        (concern_id,),
    ).fetchall()
    return {
        str(r[0]): {
            "record_ref": r[1],
            "intent": json_decode(r[2], fallback={}),
        }
        for r in rows
    }


def read_cursor(conn: sqlite3.Connection, concern_id: str) -> tuple[int, CursorToken]:
    row = conn.execute(
        "SELECT through_seq, cursor_token FROM concern_cursor WHERE concern_id = ?",
        (concern_id,),
    ).fetchone()
    if row is None:
        return 0, as_cursor_token("cur:0")
    return int(row[0]), as_cursor_token(str(row[1]))


def contiguous_through_seq(conn: sqlite3.Connection, concern_id: str) -> int:
    """Max N such that every seq 1..N is committed (contiguous prefix)."""
    rows = conn.execute(
        "SELECT seq FROM concern_journal "
        "WHERE concern_id = ? AND committed_at IS NOT NULL ORDER BY seq",
        (concern_id,),
    ).fetchall()
    through = 0
    for (seq,) in rows:
        if seq != through + 1:
            break
        through = seq
    return through


def snapshot_state(conn: sqlite3.Connection, concern_id: str) -> dict[str, Any]:
    """Operational snapshot used for checkpoints and recovery verification."""
    head = read_head(conn, concern_id)
    return {
        "head": head,
        "controls": read_controls(conn, concern_id),
        "intents": read_intents(conn, concern_id),
        "through_seq": contiguous_through_seq(conn, concern_id),
    }
