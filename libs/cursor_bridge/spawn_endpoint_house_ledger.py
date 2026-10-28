"""Append-only wake outcomes for spawn/house tabs (todo:spawn-endpoint-house-ledger)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR

CONSUMERS: tuple[str, ...] = ("mcp",)

_ROW_ID_RE = re.compile(r"^[0-9a-f]{32}$")

LEDGER_FILENAME = "cursor-bridge-tab-wake-ledger.jsonl"


def format_ledger_prompt_prefix(*, row_id: str, purpose: str) -> str:
    """Two-line ledger header for spawned first messages and conductor packets."""
    rid = str(row_id or "").strip()
    if not _ROW_ID_RE.match(rid):
        return ""
    line = str(purpose or "").strip() or "(unstated)"
    if "\n" in line:
        line = "(unstated)"
    return f"ledger_row: {rid}\npurpose: {line}\n"


def ledger_path(watch_dir: Path | None = None) -> Path:
    base = watch_dir if watch_dir is not None else WATCH_DIR
    return base / LEDGER_FILENAME


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def record_tab_wake(
    *,
    gui_host: str,
    agent_id: str,
    op: str,
    ok: bool,
    reason: str = "",
    detail: dict[str, Any] | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Append one wake/rename outcome; spawn ledger readers scan for last success."""
    aid = str(agent_id or "").strip()
    row: dict[str, Any] = {
        "at": _utcnow(),
        "gui_host": str(gui_host or "").strip(),
        "agent_id": aid,
        "op": str(op or "").strip(),
        "ok": bool(ok),
        "reason": str(reason or "").strip(),
    }
    if detail:
        row["detail"] = detail
    path = ledger_path(watch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def last_successful_wake(
    agent_id: str,
    *,
    watch_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Newest ``ok`` row for ``agent_id``, or None."""
    aid = str(agent_id or "").strip()
    if not aid:
        return None
    path = ledger_path(watch_dir)
    if not path.is_file():
        return None
    last: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("agent_id") or "").strip() != aid:
                continue
            if not row.get("ok"):
                continue
            last = row
    return last


__all__ = [
    "LEDGER_FILENAME",
    "format_ledger_prompt_prefix",
    "last_successful_wake",
    "ledger_path",
    "record_tab_wake",
]
