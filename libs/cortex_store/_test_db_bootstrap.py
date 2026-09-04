"""Test-only cortex DB bootstrap via ``run_migrations`` on a head-schema template.

The session template is materialized once by replaying migrations 001→056 on an
empty SQLite file — same chain production uses, with no live-substrate dependency.
Per-test copies keep isolation without re-running the full migration chain.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cortex_store.db import run_migrations
from cortex_store.schema_snapshot import apply_canonical_schema_snapshot

_TEMPLATE: Path | None = None
_TEMPLATE_LOCK = False


def materialize_head_schema_template(dest: Path) -> None:
    """Build a head-schema DB at *dest* from snapshot + tracked incremental migrations."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(dest)
    try:
        apply_canonical_schema_snapshot(conn)
        run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def _session_template_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    global _TEMPLATE, _TEMPLATE_LOCK
    if _TEMPLATE is not None:
        return _TEMPLATE
    if _TEMPLATE_LOCK:
        pytest.skip("Concurrent cortex DB template build — retry")
    _TEMPLATE_LOCK = True
    try:
        template_dir = tmp_path_factory.mktemp("cortex_migrated_template")
        path = template_dir / "head.db"
        materialize_head_schema_template(path)
        _TEMPLATE = path
        return path
    finally:
        _TEMPLATE_LOCK = False


def copy_template_db(template: Path, dest: Path) -> None:
    """Copy the session template to *dest* (file-level backup)."""
    src = sqlite3.connect(template)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        src.close()
        dst.close()


def open_migrated_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def fresh_migrated_connection(
    tmp_path: Path,
    template: Path,
) -> sqlite3.Connection:
    """Per-test isolated connection backed by a template copy."""
    db_path = tmp_path / "cortex_test.db"
    copy_template_db(template, db_path)
    return open_migrated_connection(db_path)
