"""Read-only store epoch witness + explicit provisioning helpers (tests/bootstrap)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

EpochStatus = Literal["ok", "unprovisioned", "unavailable"]

_EPOCH_TABLE = "_execution_store_epoch"
_EPOCH_DDL = f"""
CREATE TABLE IF NOT EXISTS {_EPOCH_TABLE} (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class EpochReadResult:
    status: EpochStatus
    epoch: str | None = None


def intent_tag_body(identity_key: str) -> str:
    """Raw intent line stored in bus turn bodies (case-sensitive, unescaped)."""
    return f"intent:{identity_key}\n"


def intent_tag_length(identity_key: str) -> int:
    return len(intent_tag_body(identity_key))


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def read_store_epoch(db_path: Path, *, timeout: float = 0.2) -> EpochReadResult:
    """Read epoch UUID from owner SQLite without writes, DDL, or COMMIT."""
    path = Path(db_path)
    if not path.exists() or path.stat().st_size == 0:
        return EpochReadResult(status="unprovisioned", epoch=None)
    uri = _read_only_uri(path)
    try:
        with closing(
            sqlite3.connect(uri, uri=True, timeout=timeout, check_same_thread=False)
        ) as conn:
            conn.execute("PRAGMA query_only=ON")
            row = conn.execute(
                f"SELECT epoch_id FROM {_EPOCH_TABLE} WHERE singleton = 1"
            ).fetchone()
            if row is None:
                return EpochReadResult(status="unprovisioned", epoch=None)
            epoch = str(row[0])
            if not epoch:
                return EpochReadResult(status="unprovisioned", epoch=None)
            return EpochReadResult(status="ok", epoch=epoch)
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "no such table" in msg:
            return EpochReadResult(status="unprovisioned", epoch=None)
        return EpochReadResult(status="unavailable", epoch=None)
    except sqlite3.Error:
        return EpochReadResult(status="unavailable", epoch=None)


def provision_store_epoch(db_path: Path) -> str:
    """Create epoch table/row when missing (tests and explicit bootstrap only)."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(_EPOCH_DDL)
        ts = datetime.now(UTC).isoformat()
        conn.execute(
            f"INSERT OR IGNORE INTO {_EPOCH_TABLE} "
            "(singleton, epoch_id, created_at) VALUES (1, ?, ?)",
            (uuid4().hex, ts),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT epoch_id FROM {_EPOCH_TABLE} WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError(f"failed to provision store epoch at {path}")
        return str(row[0])


def rotate_store_epoch(db_path: Path) -> str:
    """Assign a new epoch UUID (e.g. after backup restore) — explicit operator/test action."""
    path = Path(db_path)
    new_id = uuid4().hex
    ts = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(path, timeout=5.0)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(_EPOCH_DDL)
        existing = conn.execute(
            f"SELECT 1 FROM {_EPOCH_TABLE} WHERE singleton = 1"
        ).fetchone()
        if existing is None:
            conn.execute(
                f"INSERT INTO {_EPOCH_TABLE} (singleton, epoch_id, created_at) "
                "VALUES (1, ?, ?)",
                (new_id, ts),
            )
        else:
            conn.execute(
                f"UPDATE {_EPOCH_TABLE} SET epoch_id = ?, created_at = ? "
                "WHERE singleton = 1",
                (new_id, ts),
            )
        conn.commit()
    return new_id


def epoch_for_reserve(read: EpochReadResult) -> str:
    """String persisted on local reserve rows from a read result."""
    if read.status == "ok" and read.epoch:
        return read.epoch
    return ""
