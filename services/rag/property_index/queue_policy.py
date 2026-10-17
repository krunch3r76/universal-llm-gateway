"""Exhaustion policy for the source-level extraction queue.

Fast budget is 5 failures at a 1-minute backoff. A ``MapPartialFailureError``
whose text is not ``Prompt too large`` may be claimed again while ``attempts``
is 5, 6, or 7. Backoff at those counts is 15, 60, and 240 minutes. Dequeue
requires ``attempts < 8``, so the failure that reaches 8 is the last one.
Prompt-too-large, any other non-capacity error at attempts >= 5, and every
row at attempts >= 8 are ``dead_letter``: listed on the queue and never claimed.

``capacity`` below the ceiling holds the attempt budget and stays claimable
after its own backoff. The SQL fragments and :func:`is_dead_letter` are the
same predicate. Dequeue, ``list_extraction_queue_rows``, and the article
pipeline CASE all use :func:`queue_state_case_sql`.
"""

from __future__ import annotations

from dataclasses import dataclass

QUEUE_FAST_ATTEMPTS = 5
QUEUE_RETRY_CEILING = 8

# Minutes added to last_failure_at before the row may be claimed again.
# attempts 0-4 stay at 1 minute so the fast budget does not change.
_BACKOFF_MINUTES_SQL = (
    "CASE"
    " WHEN attempts >= 7 THEN '240'"
    " WHEN attempts >= 6 THEN '60'"
    " WHEN attempts >= 5 THEN '15'"
    " ELSE '1'"
    " END"
)

DEAD_LETTER_SQL = (
    "(IFNULL(last_failure_category, '') = 'dead_letter'"
    " OR attempts >= 8"
    " OR ("
    " attempts >= 5"
    " AND IFNULL(last_failure_category, '') != 'capacity'"
    " AND ("
    " IFNULL(last_error, '') LIKE '%Prompt too large%'"
    " OR IFNULL(last_error, '') NOT LIKE '%MapPartialFailureError%'"
    ")))"
)


@dataclass(frozen=True, slots=True)
class ExhaustedRedrive:
    """One-shot classification of rows already past the fast budget.

    ``succeeded`` and ``failed_again`` stay empty: this call arms a claim or
    writes ``dead_letter``. It does not run extraction.
    """

    dry_run: bool
    retried: tuple[str, ...]
    dead_lettered: tuple[str, ...]
    succeeded: tuple[str, ...] = ()
    failed_again: tuple[str, ...] = ()


def is_retriable_map_error(error: str | None) -> bool:
    """True when a map failure may be claimed again after the fast budget."""
    text = error or ""
    return "MapPartialFailureError" in text and "Prompt too large" not in text


def is_dead_letter(
    attempts: int,
    error: str | None,
    category: str | None,
) -> bool:
    """True when this row must leave the claim set and list as ``dead_letter``.

    Matches ``DEAD_LETTER_SQL``. ``category='capacity'`` below the ceiling
    stays claimable after backoff.
    """
    if category == "dead_letter" or attempts >= QUEUE_RETRY_CEILING:
        return True
    if attempts < QUEUE_FAST_ATTEMPTS or category == "capacity":
        return False
    if "Prompt too large" in (error or ""):
        return True
    return "MapPartialFailureError" not in (error or "")


def disposition(
    attempts: int,
    error: str | None,
    category: str | None,
) -> str:
    """Return ``retry``, ``dead_letter``, or ``skip`` for a one-shot re-drive.

    ``skip`` is a row still inside the fast budget, or ``capacity`` below the
    ceiling (the budget is held; redrive must not stamp ``dead_letter``).
    ``retry`` is a retriable map failure below the ceiling. Everything else
    past the fast budget is ``dead_letter``.
    """
    if attempts < QUEUE_FAST_ATTEMPTS and category != "dead_letter":
        return "skip"
    if is_dead_letter(attempts, error, category):
        return "dead_letter"
    if is_retriable_map_error(error) and attempts < QUEUE_RETRY_CEILING:
        return "retry"
    if category == "capacity" and attempts < QUEUE_RETRY_CEILING:
        return "skip"
    return "dead_letter"


def backoff_elapsed_sql() -> str:
    """SQL expression, true when this row's failure backoff has elapsed."""
    return (
        "datetime(last_failure_at, '+' || "
        + _BACKOFF_MINUTES_SQL
        + " || ' minutes') < datetime('now')"
    )


def backoff_pending_sql() -> str:
    """SQL expression, true when this row's failure backoff has not elapsed."""
    return (
        "datetime(last_failure_at, '+' || "
        + _BACKOFF_MINUTES_SQL
        + " || ' minutes') >= datetime('now')"
    )


def queue_state_case_sql() -> str:
    """CASE expression that assigns ``queue_state``. No bound parameters."""
    pending = backoff_pending_sql()
    return (
        " CASE"
        f" WHEN {DEAD_LETTER_SQL} THEN 'dead_letter'"
        " WHEN last_attempt_at IS NOT NULL"
        " AND (last_failure_at IS NULL"
        " OR datetime(last_failure_at) < datetime(last_attempt_at))"
        " THEN 'in_flight'"
        " WHEN last_failure_at IS NOT NULL"
        f" AND {pending}"
        " AND last_failure_category = 'capacity'"
        " THEN 'capacity_blocked'"
        " WHEN last_failure_at IS NOT NULL"
        f" AND {pending}"
        " THEN 'cooling_off'"
        " ELSE 'ready'"
        " END"
    )


def claim_filter_sql() -> str:
    """WHERE body for dequeue. Binds the retry ceiling as ``attempts < ?``."""
    return (
        "attempts < ?"
        f" AND NOT {DEAD_LETTER_SQL}"
        " AND (last_attempt_at IS NULL"
        " OR (last_failure_at IS NOT NULL"
        " AND datetime(last_failure_at) >= datetime(last_attempt_at)"
        f" AND {backoff_elapsed_sql()}))"
    )


def arm_backoff_minutes(attempts: int) -> int:
    """How far in the past to place ``last_failure_at`` so one claim is due now.

    One minute past the schedule for that attempt count: 16 at 5, 61 at 6,
    241 at 7. The ceiling is not armed.
    """
    if attempts >= 7:
        return 241
    if attempts >= 6:
        return 61
    return 16
