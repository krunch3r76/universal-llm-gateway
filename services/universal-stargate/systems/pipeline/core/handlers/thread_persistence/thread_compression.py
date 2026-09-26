"""Thread compaction summary boundary metadata (derivation_type=thread_compression).

Encodes and decodes the two turn boundaries carried by ``thread_summary(N)``
assertions: ``covered_through_turn_index`` (last summarized turn, inclusive) and
``hot_tail_start_turn_index`` (first verbatim turn). ``compaction_summarize``
writes them as compact JSON in ``reasoning_summary``; ``window.py`` reads them
back to decide where the hot tail starts, falling back to the predicate's N.
"""

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
    """Serialize compaction turn boundaries into the compact JSON ``reasoning_summary``
    string.

    Stored by ``compaction_summarize`` on ``thread_summary(N)`` assertions and later
    decoded by :func:`parse_thread_compression_boundaries`. Keyword-only ints; no
    validation that the covered index precedes the hot-tail start.
    """
    return json.dumps(
        {
            "covered_through_turn_index": covered_through_turn_index,
            "hot_tail_start_turn_index": hot_tail_start_turn_index,
        },
        separators=(",", ":"),
    )


def boundaries_from_exclusive_upper(exclusive_upper: int) -> tuple[int, int]:
    """Convert a ``thread_summary(N)`` exclusive upper bound into boundary indices.

    Returns ``(N - 1, N)``: the last covered turn (inclusive) and the first
    hot-tail turn. Used by ``compaction_summarize`` when writing summaries and as
    the predicate-form fallback in :func:`parse_thread_compression_boundaries`.
    """
    covered_through = exclusive_upper - 1
    return covered_through, exclusive_upper


def parse_thread_compression_boundaries(
    assertion: dict[str, Any],
) -> tuple[int | None, int | None]:
    """Recover ``(covered_through, hot_tail_start)`` turn indices from a summary
    assertion.

    Prefers the ``reasoning_summary`` JSON (string or dict) when both boundary keys
    hold ints; otherwise derives them from a ``thread_summary(N)`` predicate_form.
    Returns ``(None, None)`` when neither source is usable. Called by ``window.py``
    to locate the hot-tail start.
    """
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
