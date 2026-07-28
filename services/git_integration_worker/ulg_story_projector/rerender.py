"""Regenerate journal shard lines from live events (spec Bind 3 invariant c)."""

from __future__ import annotations

import re
from typing import Any

from universal_logging import get_logger

from .event_query import events_query_available, query_event_by_seq
from .journal import shard_path
from .render import render_event_line

logger = get_logger(__name__)

_SEQ_TAIL_RE = re.compile(r"\[seq:(\d+)")


def _preserve_verbatim(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("Epoch:"):
        return True
    return "retention gap" in stripped


def rerender_shard(shard_key: str) -> dict[str, Any]:
    """Re-render event lines in a shard; preserve epoch and retention-gap verbatim."""
    if not events_query_available():
        return {"ok": False, "reason": "events_query_unavailable"}

    path = shard_path(shard_key)
    if not path.is_file():
        return {"ok": False, "reason": "shard_missing", "path": str(path)}

    original_lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    rerendered = 0
    preserved = 0
    missing_seqs: list[int] = []

    for line in original_lines:
        if not line.strip():
            continue
        if _preserve_verbatim(line):
            output.append(line.rstrip())
            preserved += 1
            continue

        match = _SEQ_TAIL_RE.search(line)
        if match is None:
            output.append(line.rstrip())
            preserved += 1
            continue

        seq = int(match.group(1))
        row = query_event_by_seq(seq)
        if row is None:
            missing_seqs.append(seq)
            output.append(line.rstrip())
            preserved += 1
            continue

        rendered = render_event_line(
            seq=seq,
            signal=str(row.get("signal") or ""),
            payload=row.get("payload") or {},
        )
        if rendered is None:
            output.append(line.rstrip())
            preserved += 1
            continue

        output.append(rendered.rstrip())
        rerendered += 1

    if missing_seqs:
        return {
            "ok": False,
            "reason": "events_missing",
            "missing_seqs": missing_seqs,
            "rerendered": rerendered,
        }

    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    logger.info(
        "ulg-story shard %s re-rendered: %s lines, %s preserved verbatim",
        shard_key,
        rerendered,
        preserved,
    )
    return {
        "ok": True,
        "shard_key": shard_key,
        "rerendered": rerendered,
        "preserved": preserved,
        "total_lines": len(output),
    }
