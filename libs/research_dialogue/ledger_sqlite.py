from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def ledger_ro_uri(ledger_path: Path) -> str:
    return ledger_path.resolve().as_uri() + "?mode=ro"


def ledger_max_id(ledger_path: Path) -> int:
    conn = sqlite3.connect(ledger_ro_uri(ledger_path), uri=True)
    try:
        row = conn.execute("select coalesce(max(id),0) from entries").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def ledger_entries_after(ledger_path: Path, max_id: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(ledger_ro_uri(ledger_path), uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "select * from entries where id > ? order by id",
            (max_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
