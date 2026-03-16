#!/usr/bin/env python3
"""Migrate todo.yaml + todo.archive.yaml → todos.db (SQLite).

One-time migration script. Reads both YAML files, creates the todos table
with a context column, and inserts all items. Idempotent — re-running
replaces the DB.

Usage:
    python scripts/migrate-todos-to-db.py [--db PATH] [--tasks-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import yaml

_DEFAULT_DB = os.path.expanduser("~/.cortex/todos.db")
_DEFAULT_TASKS = "tasks"
_DEFAULT_CONTEXT = "universal-llm-gateway"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'backlog',
    domain TEXT NOT NULL DEFAULT '',
    context TEXT NOT NULL DEFAULT 'universal-llm-gateway',
    description TEXT DEFAULT '',
    complexity TEXT DEFAULT '',
    depends_on TEXT DEFAULT '',
    refs TEXT DEFAULT '{}',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_context ON todos(context);
CREATE INDEX IF NOT EXISTS idx_todos_domain ON todos(domain);
CREATE INDEX IF NOT EXISTS idx_todos_priority ON todos(priority);
"""


def _emit_event(signal: str, **payload: object) -> None:
    """Emit a lightweight JSON event line for migration observability."""
    print(json.dumps({"signal": signal, "payload": payload}, default=str))


def _parse_item(raw: dict, *, source: str) -> dict:
    """Convert one YAML todo item into the todos table schema.

    Args:
        raw: Todo item mapping from `todo.yaml` or `todo.archive.yaml`.
        source: Input source label used for source-specific behavior.

    Returns:
        Flat dictionary matching the `todos` table columns.

    Notes:
        - `refs` is normalized to a JSON object string; non-dict values become `{}`.
        - `preliminary_spec` and `implementation_notes` are merged into `notes`.
        - `depends_on` prefers `depends_on`, then `blocked_by`; lists are joined with
          a comma separator.
    """
    refs_raw = raw.get("refs", {})
    if isinstance(refs_raw, list) and not refs_raw:
        refs_raw = {}
    if not isinstance(refs_raw, dict):
        refs_raw = {}
    refs_json = json.dumps(refs_raw, default=str)

    notes_parts = [
        section
        for section in (
            (
                f"## Preliminary Spec\n{raw['preliminary_spec']}"
                if raw.get("preliminary_spec")
                else ""
            ),
            (
                f"## Implementation Notes\n{raw['implementation_notes']}"
                if raw.get("implementation_notes")
                else ""
            ),
        )
        if section
    ]
    notes = "\n\n".join(notes_parts)

    depends = raw.get("depends_on") or raw.get("blocked_by")
    if isinstance(depends, list):
        depends_str = ", ".join(str(d) for d in depends)
    elif depends is None:
        depends_str = ""
    else:
        depends_str = str(depends)

    return {
        "id": raw["id"],
        "title": raw.get("title", ""),
        "status": raw.get("status", "open"),
        "priority": raw.get("priority", "backlog"),
        "domain": raw.get("domain", ""),
        "context": _DEFAULT_CONTEXT,
        "description": (raw.get("description") or "").strip(),
        "complexity": raw.get("complexity", ""),
        "depends_on": depends_str,
        "refs": refs_json,
        "notes": notes,
    }


def _load_yaml(path: Path) -> list[dict]:
    """Load todo records from a YAML file with top-level `items`.

    Args:
        path: YAML file path. Expected format is:
            `items: [ { ...todo fields... }, ... ]`

    Returns:
        List of todo item mappings. Returns an empty list when the file is
        missing or malformed.
    """
    if not path.exists():
        print(f"  Skipping {path} (not found)")
        _emit_event("yaml.file.skipped.notfound", path=str(path))
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        print(f"  WARNING: malformed YAML in {path}: {exc}")
        _emit_event("yaml.file.skipped.malformed", path=str(path), error=str(exc))
        return []
    items = data.get("items", [])
    print(f"  Loaded {len(items)} items from {path}")
    return items


def migrate(db_path: str, tasks_dir: str) -> None:
    """Migrate `todo.yaml` and `todo.archive.yaml` into a fresh SQLite DB.

    The migration is idempotent for repeated runs against the same target path:
    if the database already exists, it is removed and recreated from source YAML.

    Args:
        db_path: Output SQLite path for `todos` data.
        tasks_dir: Directory containing `todo.yaml` and `todo.archive.yaml`.
    """
    tasks = Path(tasks_dir)
    db = Path(db_path)

    print(f"Target DB: {db}")
    print(f"Tasks dir: {tasks}")

    todo_items = _load_yaml(tasks / "todo.yaml")
    archive_items = _load_yaml(tasks / "todo.archive.yaml")

    all_items: list[dict] = []
    seen_ids: set[str] = set()

    def _process_items(
        raw_items: list[dict],
        source_name: str,
        *,
        normalize_archive_status: bool = False,
    ) -> None:
        for raw in raw_items:
            item = _parse_item(raw, source=source_name)
            if normalize_archive_status and item["status"] not in (
                "resolved",
                "done",
                "completed",
            ):
                item["status"] = "resolved"
            if item["id"] in seen_ids:
                print(f"  WARNING: duplicate id {item['id']!r} in {source_name}, skipping")
                _emit_event(
                    "todo.item.skipped.duplicateid",
                    source=source_name,
                    todo_id=item["id"],
                )
                continue
            seen_ids.add(item["id"])
            all_items.append(item)

    _process_items(todo_items, "todo.yaml")
    _process_items(archive_items, "archive", normalize_archive_status=True)

    db.parent.mkdir(parents=True, exist_ok=True)
    if db.exists():
        db.unlink()
        print(f"  Removed existing {db}")
        _emit_event("db.file.removed", path=str(db))

    inserted = 0
    with sqlite3.connect(str(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)

        for item in all_items:
            conn.execute(
                """INSERT INTO todos (id, title, status, priority, domain, context,
                   description, complexity, depends_on, refs, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item["id"],
                    item["title"],
                    item["status"],
                    item["priority"],
                    item["domain"],
                    item["context"],
                    item["description"],
                    item["complexity"],
                    item["depends_on"],
                    item["refs"],
                    item["notes"],
                ),
            )
            inserted += 1

        conn.commit()

        cursor = conn.execute("SELECT COUNT(*) FROM todos")
        count = cursor.fetchone()[0]
        cursor = conn.execute(
            "SELECT status, COUNT(*) FROM todos GROUP BY status ORDER BY status"
        )
        breakdown = {row[0]: row[1] for row in cursor.fetchall()}

    print(f"\nMigration complete: {inserted} items inserted, {count} in DB")
    _emit_event("migration.completed", inserted=inserted, count=count, db_path=str(db))
    for status, cnt in sorted(breakdown.items()):
        print(f"  {status}: {cnt}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate todo.yaml → todos.db")
    parser.add_argument(
        "--db", default=_DEFAULT_DB, help=f"DB path (default: {_DEFAULT_DB})"
    )
    parser.add_argument("--tasks-dir", default=_DEFAULT_TASKS, help="Tasks directory")
    args = parser.parse_args()
    migrate(args.db, args.tasks_dir)


if __name__ == "__main__":
    main()
