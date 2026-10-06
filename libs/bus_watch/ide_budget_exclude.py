"""Hop-retire pins that keep departing tabs out of resume-mtime budget selection.

Specimen a:38328 / agent-bus:15420 — a retired hop tab kept writing and won
mtime-newest among ``resume <root>`` openers, raising a false CONTEXT_BUDGET.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR

AGENT_TRANSCRIPTS = (
    Path.home()
    / ".cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts"
)
BUDGET_EXCLUDE_QUIESCENT_S = 300.0
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _root_slug(root_id: str) -> str:
    rid = str(root_id or "").strip()
    if not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        raise ValueError(f"invalid root_id: {root_id!r}")
    return rid


def budget_exclude_path(root_id: str, watch_dir: Path = WATCH_DIR) -> Path:
    return watch_dir / f"liaison-budget-exclude-{_root_slug(root_id)}.json"


def pin_retired_resume_transcript(
    root_id: str,
    transcript_id: str,
    *,
    watch_dir: Path = WATCH_DIR,
    now: float | None = None,
) -> dict[str, Any]:
    """Pin a departing hop tab so ``newest_resume_transcript`` cannot select it."""
    tid = str(transcript_id or "").strip()
    if not tid or not _UUID_RE.match(tid):
        return {"ok": False, "reason": "transcript_id_not_uuid"}
    path = budget_exclude_path(root_id, watch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    excluded = [
        row
        for row in (raw.get("excluded") or [])
        if isinstance(row, dict) and str(row.get("transcript_id") or "") != tid
    ]
    stamp = (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        if now is not None
        else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )
    excluded.append({"transcript_id": tid, "retired_at": stamp})
    payload = {"root": str(root_id), "excluded": excluded}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "transcript_id": tid}


def active_budget_excludes(
    root_id: str,
    *,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
    watch_dir: Path = WATCH_DIR,
    now: float | None = None,
    quiescent_s: float = BUDGET_EXCLUDE_QUIESCENT_S,
) -> set[str]:
    """Transcript ids still excluded from resume-mtime fallback.

    Drops pins whose JSONL has been quiet for ``quiescent_s`` (or missing).
    """
    path = budget_exclude_path(root_id, watch_dir)
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    ts = now if now is not None else time.time()
    live: set[str] = set()
    kept: list[dict[str, Any]] = []
    for row in raw.get("excluded") or []:
        if not isinstance(row, dict):
            continue
        tid = str(row.get("transcript_id") or "")
        if not tid or not _UUID_RE.match(tid):
            continue
        jsonl = transcripts_dir / tid / f"{tid}.jsonl"
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if (ts - mtime) < float(quiescent_s):
            live.add(tid)
            kept.append({"transcript_id": tid, "retired_at": row.get("retired_at")})
    if kept != (raw.get("excluded") or []):
        try:
            path.write_text(
                json.dumps({"root": str(root_id), "excluded": kept}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
    return live


__all__ = [
    "AGENT_TRANSCRIPTS",
    "BUDGET_EXCLUDE_QUIESCENT_S",
    "active_budget_excludes",
    "budget_exclude_path",
    "pin_retired_resume_transcript",
]
