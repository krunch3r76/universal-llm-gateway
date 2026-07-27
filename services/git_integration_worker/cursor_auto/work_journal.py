"""Append-only work journal at every cursor-auto dispatch terminal."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.git_integration_worker.config import load_config

_JOURNAL_REL = Path("notes/system/cursor-auto/work-journal.jsonl")


def journal_path(*, source_repo: Path | None = None) -> Path:
    root = source_repo if source_repo is not None else load_config().source_repo
    return root / _JOURNAL_REL


def append_journal_entry(
    *,
    thread_id: str,
    dispatch_id: str | None,
    contract: str,
    terminal_status: str,
    disposition: str,
    decisions: list[str] | None = None,
    frictions: list[str] | None = None,
    next_hint: str | None = None,
    extra: dict[str, Any] | None = None,
    source_repo: Path | None = None,
) -> bool:
    """Append one JSONL row; return False on I/O failure (non-fatal for handler)."""
    row: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "thread_id": thread_id,
        "dispatch_id": dispatch_id,
        "contract": contract,
        "terminal_status": terminal_status,
        "disposition": disposition,
        "decisions": decisions or [],
        "frictions": frictions or [],
        "next": next_hint or "",
    }
    if extra:
        row["extra"] = extra
    path = journal_path(source_repo=source_repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        return False
    return True
