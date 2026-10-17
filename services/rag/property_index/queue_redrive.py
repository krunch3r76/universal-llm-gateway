"""One-shot re-drive of extraction-queue rows past the fast budget.

Lives outside ``mixin_07`` so the redrive write is not added to that module.
The UPDATE refuses a row that became in-flight after the list snapshot, and
it does not clear ``active_execution_id``: the next claim cancels that id.
Only rows the UPDATE actually changed are reported as armed.
"""

from __future__ import annotations

from typing import Any

from .queue_policy import (
    ExhaustedRedrive,
    arm_backoff_minutes,
    disposition,
)

# A claim is in flight when last_attempt_at is set and last_failure_at is
# missing or older than the claim. Matches dequeue's in-flight predicate.
_NOT_IN_FLIGHT = (
    " AND NOT ("
    " last_attempt_at IS NOT NULL"
    " AND (last_failure_at IS NULL"
    " OR datetime(last_failure_at) < datetime(last_attempt_at))"
    " )"
)


async def redrive_exhausted_once(index: Any, *, dry_run: bool) -> ExhaustedRedrive:
    """Arm one claim for retriable rows, or persist ``dead_letter``.

    Does not reset ``attempts`` and does not run extraction. ``dry_run``
    reports the classification without writing. A write returns only sources
    whose UPDATE changed one row.
    """
    rows = index.list_extraction_queue_rows(limit=None)
    retried: list[str] = []
    dead_lettered: list[str] = []
    for row in rows:
        if row.state == "in_flight":
            continue
        kind = disposition(row.attempts, row.last_error, row.last_failure_category)
        if kind == "retry":
            retried.append(row.source)
        elif kind == "dead_letter":
            dead_lettered.append(row.source)

    if dry_run or not (retried or dead_lettered):
        return ExhaustedRedrive(
            dry_run=dry_run,
            retried=tuple(retried),
            dead_lettered=tuple(dead_lettered),
        )

    armed_retry: list[str] = []
    armed_dead: list[str] = []

    async def _write() -> None:
        conn = index._ensure_conn()
        for source in dead_lettered:
            cursor = conn.execute(
                "UPDATE extraction_queue"
                " SET last_failure_category = 'dead_letter'"
                " WHERE source = ?" + _NOT_IN_FLIGHT,
                (source,),
            )
            if cursor.rowcount == 1:
                armed_dead.append(source)
        for source in retried:
            current = conn.execute(
                "SELECT attempts FROM extraction_queue WHERE source = ?",
                (source,),
            ).fetchone()
            if current is None:
                continue
            minutes = arm_backoff_minutes(int(current[0]))
            cursor = conn.execute(
                "UPDATE extraction_queue SET"
                " last_attempt_at = datetime('now', ?),"
                " last_failure_at = datetime('now', ?)"
                " WHERE source = ?" + _NOT_IN_FLIGHT,
                (
                    f"-{minutes + 1} minutes",
                    f"-{minutes} minutes",
                    source,
                ),
            )
            if cursor.rowcount == 1:
                armed_retry.append(source)
        conn.commit()

    await index._seq.run(_write())
    return ExhaustedRedrive(
        dry_run=False,
        retried=tuple(armed_retry),
        dead_lettered=tuple(armed_dead),
    )
