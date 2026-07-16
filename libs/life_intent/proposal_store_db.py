"""SQLite connection + row codec for life_intent proposal store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_DEFAULT_DB = "/tmp/universal-protocol/life_intent_proposals.sqlite"

_store_lock = threading.Lock()
_PROCESS_EPOCH = uuid.uuid4().hex
_conn: sqlite3.Connection | None = None
_conn_path: str | None = None


def process_epoch() -> str:
    return _PROCESS_EPOCH


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def db_path() -> Path:
    raw = os.environ.get("LIFE_INTENT_PROPOSAL_DB", "").strip() or _DEFAULT_DB
    return Path(raw).expanduser().resolve()


def connect() -> sqlite3.Connection:
    global _conn, _conn_path
    path = str(db_path())
    with _store_lock:
        if _conn is not None and _conn_path == path:
            return _conn
        if _conn is not None:
            _conn.close()
            _conn = None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS life_intent_proposals (
                proposal_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                normalized_intent TEXT NOT NULL,
                work_order TEXT NOT NULL,
                verb TEXT NOT NULL,
                lane TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                packet_path TEXT,
                entity_id TEXT,
                dispatch_ref TEXT,
                dispatch_handle TEXT,
                reply_thread TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                apply_owner TEXT
            )
            """
        )
        conn.commit()
        _conn = conn
        _conn_path = path
        return conn


def reset_connection_for_tests() -> None:
    """Drop module connection cache (simulates process restart)."""
    global _conn, _conn_path, _PROCESS_EPOCH
    with _store_lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _conn_path = None
        _PROCESS_EPOCH = uuid.uuid4().hex


def encode_handle(handle: dict[str, Any] | None) -> str | None:
    if handle is None:
        return None
    return json.dumps(handle, sort_keys=True)


def decode_handle(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    return json.loads(raw)


def lock() -> threading.Lock:
    return _store_lock


def schema_version() -> int:
    return _SCHEMA_VERSION
