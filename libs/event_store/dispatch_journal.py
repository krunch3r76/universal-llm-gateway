"""Read-only helpers for cold async-dispatch journal summaries."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def dispatch_journal_path() -> Path:
    """Return the service's cold async-dispatch journal path."""
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    return data_dir / "pipeline-dispatch.db"


def fetch_dispatch_journal_summary(execution_id: str) -> dict[str, Any] | None:
    """Return a compact terminal-record summary, if the cold journal has one."""
    path = dispatch_journal_path()
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT pipeline, status, caller_agent, started_at, completed_at, record_json
                FROM dispatch_records
                WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Failed to read dispatch journal for %s: %s", execution_id, exc)
        return None
    if row is None:
        return None
    pipeline, status, caller_agent, started_at, completed_at, record_json = row
    summary: dict[str, Any] = {
        "pipeline": pipeline,
        "status": status,
        "caller_agent": caller_agent,
        "started_at": started_at,
        "completed_at": completed_at,
    }
    try:
        record = json.loads(record_json)
    except (TypeError, json.JSONDecodeError):
        record = {}
    result = record.get("result") if isinstance(record, dict) else None
    if isinstance(result, dict):
        if result.get("model"):
            summary["model"] = result["model"]
        if result.get("model_entity_id"):
            summary["model_entity_id"] = result["model_entity_id"]
    return summary
