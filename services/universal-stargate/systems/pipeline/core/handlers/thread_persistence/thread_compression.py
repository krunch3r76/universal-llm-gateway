"""Thread compaction summary boundary metadata (derivation_type=thread_compression)."""

from __future__ import annotations

import json
from typing import Any

from .turn_assertions import THREAD_SUMMARY_PREFIX, parse_thread_summary_index

_BOUNDARY_KEYS = frozenset({"covered_through_turn_index", "hot_tail_start_turn_index"})


def thread_compression_reasoning_summary(
    *,
    covered_through_turn_index: int,
    hot_tail_start_turn_index: int,
) -> str:
    """JSON boundary metadata stored on summary assertions."""
    return json.dumps(
        {
            "covered_through_turn_index": covered_through_turn_index,
            "hot_tail_start_turn_index": hot_tail_start_turn_index,
        },
        separators=(",", ":"),
    )


def boundaries_from_exclusive_upper(exclusive_upper: int) -> tuple[int, int]:
    """Map ``thread_summary(N)`` exclusive upper bound to inclusive/hot-tail indices."""
    covered_through = exclusive_upper - 1
    return covered_through, exclusive_upper


def parse_thread_compression_boundaries(
    assertion: dict[str, Any],
) -> tuple[int | None, int | None]:
    """Read boundary fields from reasoning_summary JSON or predicate_form fallback."""
    raw = assertion.get("reasoning_summary")
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and _BOUNDARY_KEYS <= parsed.keys():
            covered = parsed.get("covered_through_turn_index")
            hot_start = parsed.get("hot_tail_start_turn_index")
            if isinstance(covered, int) and isinstance(hot_start, int):
                return covered, hot_start

    pred = assertion.get("predicate_form") or ""
    if not pred.startswith(THREAD_SUMMARY_PREFIX):
        return None, None
    exclusive = parse_thread_summary_index(pred)
    if exclusive is None:
        return None, None
    return boundaries_from_exclusive_upper(exclusive)


__all__ = [
    "boundaries_from_exclusive_upper",
    "parse_thread_compression_boundaries",
    "thread_compression_reasoning_summary",
]
