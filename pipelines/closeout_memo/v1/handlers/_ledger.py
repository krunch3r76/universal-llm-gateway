"""SQLite ledger for pipeline closeout-memo dedupe, delivery, and retry."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS closeout_memos (
    memo_id TEXT PRIMARY KEY,
    memo_key TEXT NOT NULL UNIQUE,
    wake_lane TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_at REAL,
    last_error TEXT,
    receipt TEXT,
    registration_id TEXT,
    url TEXT,
    resolution_path TEXT,
    streaming_at_paste INTEGER,
    payload_json TEXT NOT NULL,
    rendered_text TEXT,
    rendered_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS closeout_memo_pages (
    wake_lane TEXT PRIMARY KEY,
    last_page_at REAL NOT NULL
);
"""


def ledger_path() -> Path:
    """Durable path. Tests set ``CLOSEOUT_MEMO_LEDGER``."""
    override = os.environ.get("CLOSEOUT_MEMO_LEDGER", "").strip()
    if override:
        return Path(override)
    data = os.environ.get("DATA_DIR", "").strip() or "/tmp"
    return Path(data) / "closeout_memo.sqlite"


def connect() -> sqlite3.Connection:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    return conn


def _now() -> float:
    return time.time()


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def insert_admit(payload: dict[str, Any]) -> str:
    """INSERT OR IGNORE. Returns ``admitted`` or ``deduped``."""
    now = _iso()
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "INSERT OR IGNORE INTO closeout_memos ("
            "memo_id, memo_key, wake_lane, kind, status, state, attempts, "
            "payload_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, 'admitted', 0, ?, ?, ?)",
            (
                payload["memo_id"],
                payload["memo_key"],
                payload["wake_lane"],
                payload["kind"],
                payload["status"],
                json.dumps(payload, sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        return "admitted" if cur.rowcount == 1 else "deduped"


def claim_admitted(wake_lane: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Move up to ``limit`` admitted rows for the lane to ``delivering``."""
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM closeout_memos WHERE wake_lane=? AND state='admitted' "
            "ORDER BY created_at ASC LIMIT ?",
            (wake_lane, limit),
        ).fetchall()
        claimed: list[dict[str, Any]] = []
        now = _iso()
        for row in rows:
            cur = conn.execute(
                "UPDATE closeout_memos SET state='delivering', updated_at=? "
                "WHERE memo_id=? AND state='admitted'",
                (now, row["memo_id"]),
            )
            if cur.rowcount == 1:
                claimed.append(dict(row))
        conn.commit()
        return claimed


def store_render(
    memo_ids: list[str], *, text: str, sha256: str
) -> None:
    now = _iso()
    with _LOCK, connect() as conn:
        for memo_id in memo_ids:
            conn.execute(
                "UPDATE closeout_memos SET rendered_text=?, rendered_sha256=?, "
                "updated_at=? WHERE memo_id=?",
                (text, sha256, now, memo_id),
            )


def mark_delivered(
    memo_ids: list[str],
    *,
    receipt: str,
    registration_id: str | None,
    url: str | None,
    resolution_path: str | None,
    streaming_at_paste: bool | None,
) -> None:
    now = _iso()
    flag = None if streaming_at_paste is None else int(bool(streaming_at_paste))
    with _LOCK, connect() as conn:
        for memo_id in memo_ids:
            conn.execute(
                "UPDATE closeout_memos SET state='delivered', receipt=?, "
                "registration_id=?, url=?, resolution_path=?, "
                "streaming_at_paste=?, updated_at=?, next_at=NULL "
                "WHERE memo_id=?",
                (
                    receipt,
                    registration_id,
                    url,
                    resolution_path,
                    flag,
                    now,
                    memo_id,
                ),
            )


def schedule_retry(
    memo_ids: list[str], *, delay_s: float, error: str
) -> None:
    now_iso = _iso()
    next_at = _now() + delay_s
    with _LOCK, connect() as conn:
        for memo_id in memo_ids:
            conn.execute(
                "UPDATE closeout_memos SET state='delivering', attempts=attempts+1, "
                "next_at=?, last_error=?, updated_at=? WHERE memo_id=?",
                (next_at, error, now_iso, memo_id),
            )


def mark_undelivered(memo_ids: list[str], *, error: str) -> None:
    now = _iso()
    with _LOCK, connect() as conn:
        for memo_id in memo_ids:
            conn.execute(
                "UPDATE closeout_memos SET state='undelivered', last_error=?, "
                "updated_at=?, next_at=NULL WHERE memo_id=? AND state!='delivered'",
                (error, now, memo_id),
            )


def due_delivering(*, now: float | None = None) -> list[dict[str, Any]]:
    """Delivering rows whose ``next_at`` has arrived."""
    moment = _now() if now is None else now
    with _LOCK, connect() as conn:
        rows = conn.execute(
            "SELECT * FROM closeout_memos WHERE state='delivering' "
            "AND next_at IS NOT NULL AND next_at<=?",
            (moment,),
        ).fetchall()
    return [dict(row) for row in rows]


def claim_page(wake_lane: str, *, window_s: float = 900.0) -> bool:
    """True when this lane may page now. One page per lane per window."""
    moment = _now()
    with _LOCK, connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT last_page_at FROM closeout_memo_pages WHERE wake_lane=?",
            (wake_lane,),
        ).fetchone()
        if row is not None and moment - float(row["last_page_at"]) < window_s:
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO closeout_memo_pages (wake_lane, last_page_at) VALUES (?, ?) "
            "ON CONFLICT(wake_lane) DO UPDATE SET last_page_at=excluded.last_page_at",
            (wake_lane, moment),
        )
        conn.commit()
        return True


def load(memo_id: str) -> dict[str, Any] | None:
    with _LOCK, connect() as conn:
        row = conn.execute(
            "SELECT * FROM closeout_memos WHERE memo_id=?", (memo_id,)
        ).fetchone()
    return dict(row) if row is not None else None
