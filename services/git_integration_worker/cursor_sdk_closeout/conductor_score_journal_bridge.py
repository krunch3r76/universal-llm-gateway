"""GIW bridge for conductor-authored score journal appends (UTC clocks only)."""

from __future__ import annotations

from datetime import UTC, datetime

from implement_admission.conductor_score_io import JournalRecord
from implement_admission.conductor_score_journal import append_journal_record


def append_conductor_score_journal_record(
    slug: str,
    *,
    prior_tip_sha: str | None,
    tip_sha: str,
    tip_body: str | None = None,
    seat: str,
    dispatch_id: str | None,
    reason: str,
    rows: tuple[str, ...],
    delta: str,
) -> None:
    """Append one journal row with ``datetime.now(UTC).isoformat()`` written_at."""
    append_journal_record(
        slug,
        JournalRecord(
            prior_tip_sha=prior_tip_sha,
            tip_sha=tip_sha,
            tip_body=tip_body or "",
            seat=seat,
            dispatch_id=dispatch_id,
            reason=reason,
            rows=rows,
            delta=delta,
            written_at=datetime.now(UTC).isoformat(),
        ),
    )


__all__ = ["append_conductor_score_journal_record"]
