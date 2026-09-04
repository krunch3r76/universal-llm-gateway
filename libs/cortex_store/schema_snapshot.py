"""SQLite schema snapshot helpers for migration-tree drift guards."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
LIVE_SNAPSHOT_PATH = _FIXTURES_DIR / "live_schema_snapshot.json"
ALLOWLIST_PATH = _FIXTURES_DIR / "schema_benign_allowlist.json"
DEFAULT_LIVE_DB_PATH = Path(
    os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))
)


def dump_sqlite_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Capture tables, columns, and indexes from ``sqlite_master``."""
    schema: dict[str, Any] = {"tables": {}, "indexes": {}}

    tables = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for name, sql in tables:
        cols = conn.execute(f"PRAGMA table_info({name})").fetchall()
        schema["tables"][name] = {
            "sql": sql,
            "columns": [(c[1], c[2], c[3], c[4], c[5]) for c in cols],
        }

    indexes = conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master "
        "WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for name, tbl, sql in indexes:
        schema["indexes"][name] = {"table": tbl, "sql": sql}

    return schema


def load_canonical_live_snapshot() -> dict[str, Any]:
    return json.loads(LIVE_SNAPSHOT_PATH.read_text())


def load_benign_allowlist() -> dict[str, Any]:
    return json.loads(ALLOWLIST_PATH.read_text())


def apply_canonical_schema_snapshot(conn: sqlite3.Connection) -> None:
    """Materialize head-schema DDL from the canonical snapshot fixture.

    Lane-B worktrees and CI sandboxes only track incremental migrations under
    ``libs/cortex_store/migrations/``; the 001–038 base chain lives gitignored
    on the shared checkout. Tests bootstrap from this snapshot, then apply the
    tracked incremental migration files idempotently via ``run_migrations``.
    """
    snapshot = load_canonical_live_snapshot()
    conn.execute("PRAGMA foreign_keys=ON")

    def _exec_idempotent(sql: str) -> None:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError as exc:
            if "already exists" not in str(exc):
                raise

    for table in sorted(snapshot["tables"]):
        sql = snapshot["tables"][table].get("sql")
        if sql:
            _exec_idempotent(sql)
    for index in sorted(snapshot["indexes"]):
        sql = snapshot["indexes"][index].get("sql")
        if sql:
            _exec_idempotent(sql)
    conn.commit()


def open_live_db_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection to the live cortex DB (never mutates)."""
    path = db_path or DEFAULT_LIVE_DB_PATH
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def refresh_live_schema_snapshot(
    *,
    db_path: Path | None = None,
    dest: Path | None = None,
) -> Path:
    """Dump live ``sqlite_master`` to the canonical snapshot fixture (read-only).

    Run after a legitimate migration changes the live schema::

        python -m cortex_store.schema_snapshot --refresh
    """
    target = dest or LIVE_SNAPSHOT_PATH
    conn = open_live_db_readonly(db_path)
    try:
        schema = dump_sqlite_schema(conn)
    finally:
        conn.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return target


def _column_tuple(column: list[Any] | tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(column)


def diff_schemas(
    replay: dict[str, Any],
    live: dict[str, Any],
    allowlist: dict[str, Any],
) -> list[str]:
    """Return human-readable non-benign divergences (empty == pass)."""
    issues: list[str] = []

    allowed_tables = set(allowlist.get("replay_only_tables", []))
    allowed_columns = {
        (table, col)
        for table, cols in allowlist.get("replay_only_columns", {}).items()
        for col in cols
    }
    allowed_indexes = set(allowlist.get("replay_only_indexes", []))

    live_tables = set(live["tables"])
    replay_tables = set(replay["tables"])
    for missing in sorted(live_tables - replay_tables):
        issues.append(f"table missing from replay: {missing}")

    for extra in sorted(replay_tables - live_tables):
        if extra not in allowed_tables:
            issues.append(f"unexpected replay-only table: {extra}")

    for table in sorted(live_tables & replay_tables):
        live_cols = {c[0]: _column_tuple(c) for c in live["tables"][table]["columns"]}
        replay_cols = {
            c[0]: _column_tuple(c) for c in replay["tables"][table]["columns"]
        }
        for col in sorted(live_cols.keys() - replay_cols.keys()):
            issues.append(f"column missing from replay: {table}.{col}")
        for col in sorted(replay_cols.keys() - live_cols.keys()):
            if (table, col) not in allowed_columns:
                issues.append(f"unexpected replay-only column: {table}.{col}")
        for col in sorted(live_cols.keys() & replay_cols.keys()):
            if live_cols[col] != replay_cols[col]:
                issues.append(f"column definition mismatch: {table}.{col}")

    live_indexes = set(live["indexes"])
    replay_indexes = set(replay["indexes"])
    for missing in sorted(live_indexes - replay_indexes):
        issues.append(f"index missing from replay: {missing}")
    for extra in sorted(replay_indexes - live_indexes):
        if extra not in allowed_indexes:
            issues.append(f"unexpected replay-only index: {extra}")

    return issues


def find_numbered_migrations_outside_canonical_tree(
    repo_root: Path,
    *,
    canonical_dir: Path | None = None,
) -> list[Path]:
    """G1: numbered ``NNN_*.{sql,py}`` files outside the canonical migrations dir."""
    canonical = canonical_dir or Path(__file__).parent / "migrations"
    canonical_resolved = canonical.resolve()
    offenders: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".sql", ".py"}:
            continue
        if path.name.startswith("__"):
            continue
        stem = path.stem
        if not stem[:3].isdigit():
            continue
        if path.resolve().is_relative_to(canonical_resolved):
            continue
        offenders.append(path)
    return sorted(offenders)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cortex schema snapshot utilities")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-dump ~/.cortex/cortex.db into fixtures/live_schema_snapshot.json",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override live DB path (default: CORTEX_DB_PATH or ~/.cortex/cortex.db)",
    )
    args = parser.parse_args()
    if args.refresh:
        path = refresh_live_schema_snapshot(db_path=args.db)
        print(f"Wrote {path}")
    else:
        parser.print_help()
