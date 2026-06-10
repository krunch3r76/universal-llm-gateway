"""Drift guards for the canonical cortex_store migration tree (G1 + G2)."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from cortex_store._test_db_bootstrap import materialize_head_schema_template
from cortex_store.db import run_migrations
from cortex_store.schema_snapshot import (
    diff_schemas,
    dump_sqlite_schema,
    find_numbered_migrations_outside_canonical_tree,
    load_benign_allowlist,
    load_canonical_live_snapshot,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def test_g1_no_numbered_migrations_outside_canonical_tree() -> None:
    offenders = find_numbered_migrations_outside_canonical_tree(
        _REPO_ROOT,
        canonical_dir=_CANONICAL_MIGRATIONS,
    )
    assert offenders == [], (
        "numbered migration files outside libs/cortex_store/migrations: "
        + ", ".join(str(p.relative_to(_REPO_ROOT)) for p in offenders)
    )


def test_g2_empty_replay_matches_live_snapshot_modulo_allowlist() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db") as handle:
        conn = sqlite3.connect(handle.name)
        conn.execute("PRAGMA foreign_keys=ON")
        run_migrations(conn)
        replay = dump_sqlite_schema(conn)
        conn.close()

    live = load_canonical_live_snapshot()
    allowlist = load_benign_allowlist()
    issues = diff_schemas(replay, live, allowlist)
    assert issues == [], "replay-vs-live schema drift:\n" + "\n".join(issues)


def test_g2_head_schema_template_matches_live_modulo_allowlist() -> None:
    """Session template path (used by conftest) must agree with the same guard."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "head.db"
        materialize_head_schema_template(db_path)
        conn = sqlite3.connect(db_path)
        replay = dump_sqlite_schema(conn)
        conn.close()

    issues = diff_schemas(
        replay, load_canonical_live_snapshot(), load_benign_allowlist()
    )
    assert issues == [], "head-template-vs-live schema drift:\n" + "\n".join(issues)
