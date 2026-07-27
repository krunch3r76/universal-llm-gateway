"""Phase 1 shadow-diff harness — persist old vs kernel decisions per tick."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from scripts.model_manager.ui.controller.charter_runner.kernel import (
    _SHADOW_DIFF_PATH,
    record_shadow_pass,
    run_shadow_for_roots,
)

_DEFAULT_PATH = _SHADOW_DIFF_PATH


def _open_harness_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or _DEFAULT_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_diff (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL,
          root TEXT NOT NULL,
          old_decision TEXT NOT NULL,
          kernel_transition TEXT NOT NULL,
          classification TEXT
        )
        """
    )
    conn.commit()
    return conn


def load_shadow_rows(
    *,
    db_path: Path | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conn = _open_harness_db(db_path)
    try:
        cur = conn.execute(
            """
            SELECT ts, root, old_decision, kernel_transition, classification
            FROM shadow_diff ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "ts": r[0],
                "root": r[1],
                "old_decision": r[2],
                "kernel_transition": r[3],
                "classification": r[4],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def export_sample_row(*, db_path: Path | None = None) -> dict[str, Any] | None:
    rows = load_shadow_rows(db_path=db_path, limit=1)
    return rows[0] if rows else None


__all__ = ["export_sample_row", "load_shadow_rows", "record_shadow_pass"]
