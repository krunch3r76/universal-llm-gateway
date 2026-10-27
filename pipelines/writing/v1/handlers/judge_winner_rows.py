"""Filter flattened semantic_judge rows to one candidate."""

from __future__ import annotations

from typing import Any


def winner_judge_rows(
    rows: list[dict[str, Any]],
    winner_id: str | None,
) -> list[dict[str, Any]]:
    if not winner_id:
        return [row for row in rows if isinstance(row, dict)]
    return [
        row
        for row in rows
        if isinstance(row, dict) and str(row.get("candidate_id") or "") == winner_id
    ]
