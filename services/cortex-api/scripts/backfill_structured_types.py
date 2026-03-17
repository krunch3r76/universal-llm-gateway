"""One-shot backfill: promote string columns to JSON-encoded structured types.

Idempotent — valid JSON values of the expected type are left untouched.
Plain-text strings are wrapped in a single-element list.
NULL values are skipped.

Run:
    python services/cortex-api/scripts/backfill_structured_types.py [--dry-run]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
TODOS_DB = Path.home() / ".cortex" / "todos.db"

MIGRATIONS: list[dict[str, Any]] = [
    {
        "db": CORTEX_DB,
        "table": "entities",
        "pk": "id",
        "columns": {
            "aliases": list,
            "attributes": dict,
        },
    },
    {
        "db": CORTEX_DB,
        "table": "assertions",
        "pk": "id",
        "columns": {
            "evidence_uris": list,
        },
    },
    {
        "db": CORTEX_DB,
        "table": "session_journals",
        "pk": "id",
        "columns": {
            "domains": list,
            "decisions": list,
            "open_items": list,
        },
    },
    {
        "db": TODOS_DB,
        "table": "todos",
        "pk": "id",
        "columns": {
            "refs": dict,
        },
    },
]


def _try_parse(value: str) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass
    # Handle double-encoded JSON: literal backslash-quote in the stored string
    if "\\" in value:
        try:
            return json.loads(value.replace('\\"', '"'))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _coerce(value: str, expected_type: type) -> str | None:
    """Return JSON string if coercion needed, or None if already valid."""
    parsed = _try_parse(value)
    if isinstance(parsed, expected_type):
        canonical = json.dumps(parsed)
        if canonical != value:
            return canonical
        return None

    if expected_type is list:
        if isinstance(parsed, str):
            return json.dumps([parsed])
        return json.dumps([value])

    if expected_type is dict:
        if isinstance(parsed, str):
            return json.dumps({"value": parsed})
        return json.dumps({"value": value})

    return None


def backfill(*, dry_run: bool = False) -> None:
    updated = 0
    skipped = 0
    errors = 0

    for spec in MIGRATIONS:
        db_path: Path = spec["db"]
        if not db_path.exists():
            print(f"  SKIP {db_path} — not found")
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        table = spec["table"]
        pk = spec["pk"]
        columns: dict[str, type] = spec["columns"]

        col_list = ", ".join([pk, *columns.keys()])
        rows = conn.execute(f"SELECT {col_list} FROM {table}").fetchall()
        print(
            f"\n  {db_path.name}/{table}: {len(rows)} rows, columns: {list(columns.keys())}"
        )

        for row in rows:
            row_id = row[pk]
            updates: dict[str, str] = {}

            for col, expected in columns.items():
                val = row[col]
                if val is None:
                    continue
                new_val = _coerce(val, expected)
                if new_val is not None:
                    updates[col] = new_val

            if not updates:
                skipped += 1
                continue

            set_clause = ", ".join(f"{c} = ?" for c in updates)
            params = [*updates.values(), row_id]

            if dry_run:
                for col, new_val in updates.items():
                    print(
                        f"    DRY-RUN {pk}={row_id!r} {col}: {row[col]!r:.60} → {new_val:.60}"
                    )
                updated += len(updates)
            else:
                try:
                    conn.execute(
                        f"UPDATE {table} SET {set_clause} WHERE {pk} = ?",
                        params,
                    )
                    for col, new_val in updates.items():
                        print(
                            f"    UPDATED {pk}={row_id!r} {col}: {row[col]!r:.60} → {new_val:.60}"
                        )
                    updated += len(updates)
                except Exception as exc:
                    print(f"    ERROR {pk}={row_id!r}: {exc}")
                    errors += 1

        if not dry_run:
            conn.commit()
        conn.close()

    prefix = "DRY-RUN " if dry_run else ""
    print(
        f"\n{prefix}Done: {updated} fields updated, {skipped} rows unchanged, {errors} errors"
    )


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"Backfill structured types {'(DRY RUN)' if dry else '(LIVE)'}")
    backfill(dry_run=dry)
