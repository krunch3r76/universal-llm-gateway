"""Filter flattened semantic_judge rows to one candidate."""

from __future__ import annotations

from typing import Any


def winner_judge_rows(
    rows: list[dict[str, Any]],
    winner_id: str | None,
) -> list[dict[str, Any]]:
    if not winner_id:
        return [row for row in rows if isinstance(row, dict)]
    wid = str(winner_id)
    kept: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("candidate_id") or "")
        if not cid or cid == wid:
            kept.append(row)
    return kept
