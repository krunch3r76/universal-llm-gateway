"""Backfill legacy thread compaction rows (derivation_type=compression → thread_compression).

Thread summarize wrote ``derivation_type='compression'`` with ``predicate_form=
thread_summary(N)`` before ``thread_compression`` landed (84114e9a). Document-ingestion
``compression`` rows (``chunk_id`` + non-thread predicate) are out of scope.

Boundary metadata is stored on ``assertions.reasoning_summary`` as compact JSON
(``covered_through_turn_index``, ``hot_tail_start_turn_index``), matching
``thread_compression_reasoning_summary`` in the pipeline handler.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from universal_logging import get_logger

from .db import table_exists

logger = get_logger("cortex-api.thread_compression_backfill")

_THREAD_SUMMARY_PREFIX = "thread_summary("
_LEGACY_SELECT = (
    "SELECT id, predicate_form, reasoning_summary, chunk_id, claim "
    "FROM assertions "
    "WHERE derivation_type = 'compression' "
    "AND predicate_form LIKE 'thread_summary(%'"
)


@dataclass
class ThreadCompressionBackfillCounts:
    """Per-run counts for legacy thread-summary derivation backfill."""

    assertions_updated: int = 0
    assertions_skipped: int = 0
    chunk_id_cleared: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)


def parse_thread_summary_index(predicate_form: str) -> int | None:
    """Extract N from ``thread_summary(N)`` (exclusive upper bound)."""
    if not predicate_form.startswith(_THREAD_SUMMARY_PREFIX):
        return None
    try:
        return int(predicate_form.split("(", 1)[1].rstrip(")"))
    except (ValueError, IndexError):
        return None


def boundaries_from_exclusive_upper(exclusive_upper: int) -> tuple[int, int]:
    """Map exclusive upper bound to inclusive covered-through and hot-tail start."""
    covered_through = exclusive_upper - 1
    return covered_through, exclusive_upper


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


def _parse_existing_boundaries(
    reasoning_summary: str | None,
) -> tuple[int, int] | None:
    if not reasoning_summary:
        return None
    try:
        parsed = json.loads(reasoning_summary)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    covered = parsed.get("covered_through_turn_index")
    hot_start = parsed.get("hot_tail_start_turn_index")
    if isinstance(covered, int) and isinstance(hot_start, int):
        return covered, hot_start
    return None


def planned_thread_compression_update(
    *,
    predicate_form: str | None,
    reasoning_summary: str | None,
    chunk_id: str | None,
) -> dict[str, object] | None:
    """Planned UPDATE fields, or ``None`` when this row should be skipped."""
    pred = predicate_form or ""
    exclusive = parse_thread_summary_index(pred)
    if exclusive is None:
        return None

    covered, hot_start = boundaries_from_exclusive_upper(exclusive)
    existing = _parse_existing_boundaries(reasoning_summary)
    if existing is not None:
        covered, hot_start = existing

    updates: dict[str, object] = {
        "derivation_type": "thread_compression",
        "reasoning_summary": thread_compression_reasoning_summary(
            covered_through_turn_index=covered,
            hot_tail_start_turn_index=hot_start,
        ),
    }
    if chunk_id is not None:
        updates["chunk_id"] = None
    return updates


def _record_skip(counts: ThreadCompressionBackfillCounts, reason: str) -> None:
    counts.assertions_skipped += 1
    counts.skip_reasons[reason] = counts.skip_reasons.get(reason, 0) + 1


def run_thread_compression_backfill(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> ThreadCompressionBackfillCounts:
    """Rewrite legacy thread-summary ``compression`` rows to ``thread_compression``."""
    counts = ThreadCompressionBackfillCounts()
    if not table_exists(conn, "assertions"):
        logger.info("assertions table absent — skipping thread_compression backfill")
        return counts

    rows = conn.execute(_LEGACY_SELECT).fetchall()
    for row in rows:
        pred = row["predicate_form"] or ""
        if not re.match(r"^thread_summary\(\d+\)\s*$", pred):
            _record_skip(counts, "predicate_form_unparseable")
            continue

        planned = planned_thread_compression_update(
            predicate_form=row["predicate_form"],
            reasoning_summary=row["reasoning_summary"],
            chunk_id=row["chunk_id"],
        )
        if planned is None:
            _record_skip(counts, "boundary_derivation_failed")
            continue

        counts.assertions_updated += 1
        if planned.get("chunk_id") is None and row["chunk_id"] is not None:
            counts.chunk_id_cleared += 1

        if dry_run:
            logger.info(
                "dry-run thread_compression backfill id=%s pred=%s",
                row["id"],
                pred,
            )
            continue

        conn.execute(
            "UPDATE assertions SET derivation_type = ?, reasoning_summary = ?, "
            "chunk_id = ? WHERE id = ? AND derivation_type = 'compression'",
            (
                planned["derivation_type"],
                planned["reasoning_summary"],
                planned.get("chunk_id", row["chunk_id"]),
                row["id"],
            ),
        )

    if not dry_run and counts.assertions_updated:
        logger.info(
            "Thread compression derivation backfill: updated=%d skipped=%s "
            "chunk_id_cleared=%d",
            counts.assertions_updated,
            counts.skip_reasons,
            counts.chunk_id_cleared,
        )
    return counts


__all__ = [
    "ThreadCompressionBackfillCounts",
    "boundaries_from_exclusive_upper",
    "parse_thread_summary_index",
    "planned_thread_compression_update",
    "run_thread_compression_backfill",
    "thread_compression_reasoning_summary",
]
