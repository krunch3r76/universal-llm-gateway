"""Dream state cursor handlers — load and save pipeline progress.

The cursor table lives in cortex.db but is pipeline-internal state (not
knowledge graph data). Direct SQLite access is appropriate here — the cursor
is not exposed via cortex-api REST and is only read/written by this pipeline.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get("CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db"))


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class CursorLoadHandler(BaseHandler):
    step_type = "dream_state_cursor_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        conn = _get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dream_state_cursor (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    last_processed_id INTEGER,
                    run_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    pipeline_version TEXT NOT NULL,
                    assertions_processed INTEGER NOT NULL DEFAULT 0,
                    actions_taken INTEGER NOT NULL DEFAULT 0
                )
            """)
            row = conn.execute(
                "SELECT last_processed_id, completed_at "
                "FROM dream_state_cursor ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()

        if row:
            result: dict[str, Any] = {
                "last_processed_id": row["last_processed_id"],
                "run_count": 1,
                "last_run_at": row["completed_at"],
            }
        else:
            result = {
                "last_processed_id": None,
                "run_count": 0,
                "last_run_at": None,
            }

        return StepOutput(raw=str(result), json=result)


class CursorSaveHandler(BaseHandler):
    step_type = "dream_state_cursor_save_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        collect_out = context.get_output("collect_assertions")
        apply_out = context.get_output("guarded_apply")

        total = 0
        max_id = 0
        if collect_out and collect_out.json:
            total = collect_out.json.get("total_assertions", 0)
            max_id = collect_out.json.get("max_assertion_id", 0)

        actions = 0
        if apply_out and apply_out.json:
            actions = len(apply_out.json.get("actions_taken", []))

        run_id = uuid.uuid4().hex[:8]
        now = datetime.now(UTC).isoformat()

        conn = _get_conn()
        try:
            conn.execute(
                "INSERT INTO dream_state_cursor "
                "(last_processed_id, run_id, started_at, completed_at, "
                "pipeline_version, assertions_processed, actions_taken) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    max_id,
                    run_id,
                    context.started_at.isoformat(),
                    now,
                    "1.0",
                    total,
                    actions,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        result = {"cursor_saved": True, "new_last_processed_id": max_id}
        return StepOutput(raw=str(result), json=result)
