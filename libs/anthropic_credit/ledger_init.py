"""Explicit one-time Anthropic credit ledger initialization (operator / staging)."""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import CreditConfig, load_credit_config
from .ledger import (
    _CREATED_AT_INDEX_SQL,
    _ENTRIES_SCHEMA_SQL,
    _LEDGER_SCHEMA_VERSION,
    _META_SCHEMA_SQL,
    _REQUEST_KIND_INDEX_SQL,
    _RESERVE_UNIQUE_INDEX_SQL,
    _WINDOW_INDEX_SQL,
    LedgerUnavailable,
    validate_ledger_path,
)


class LedgerAlreadyExists(LedgerUnavailable):
    """Raised when init is called on an existing ledger file."""


def init_ledger(cfg: CreditConfig | None = None, *, config_path: Path | None = None) -> Path:
    """Create a fresh ledger file with a ``meta(instance_id, created_at)`` row.

    Refuses when the target file already exists.
    """
    if cfg is None:
        cfg = load_credit_config(config_path)
    target = validate_ledger_path(cfg.ledger_path)
    if target.is_file():
        raise LedgerAlreadyExists(f"ledger already exists: {target}")

    parent = target.parent
    created_parent = False
    try:
        if not parent.is_dir():
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            created_parent = True
        elif not os.access(parent, os.W_OK):
            raise LedgerUnavailable(f"ledger parent not writable: {parent}")
        if created_parent:
            os.chmod(parent, 0o700)
    except OSError as exc:
        raise LedgerUnavailable(
            f"failed to create ledger parent directory {parent}: {exc}"
        ) from exc

    instance_id = str(uuid.uuid4())
    created_at = datetime.now(tz=UTC).isoformat()
    try:
        with sqlite3.connect(str(target), isolation_level=None) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(_ENTRIES_SCHEMA_SQL)
            conn.execute(_META_SCHEMA_SQL)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('instance_id', ?)",
                (instance_id,),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('created_at', ?)",
                (created_at,),
            )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (_LEDGER_SCHEMA_VERSION,),
            )
            conn.execute(_WINDOW_INDEX_SQL)
            conn.execute(_REQUEST_KIND_INDEX_SQL)
            conn.execute(_CREATED_AT_INDEX_SQL)
            conn.execute(_RESERVE_UNIQUE_INDEX_SQL)
    except (sqlite3.Error, OSError) as exc:
        raise LedgerUnavailable(f"failed to initialize ledger at {target}: {exc}") from exc

    return target


def ledger_instance_id(ledger_path: Path | str) -> str | None:
    """Return ``instance_id`` from meta when present."""
    path = Path(ledger_path)
    if not path.is_file():
        return None
    try:
        with sqlite3.connect(str(path), isolation_level=None) as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'instance_id'"
            ).fetchone()
            return str(row[0]) if row else None
    except sqlite3.Error:
        return None

