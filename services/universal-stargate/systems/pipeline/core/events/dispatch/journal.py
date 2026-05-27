"""Dispatch journal event factories (sqlite-backed terminal-record persistence).

Node-scoped signals (``scope="node"``) emitted by the dispatch journal subsystem
inside ``core/execution/dispatch_journal.py``. Cover the journal lifecycle:
write on terminal transition, read on tracker fallback, prune on retention
sweep.

Signals: ``pipeline.dispatch.journal.{written,read,pruned}``.
"""

from __future__ import annotations

from universal_event_bus import Event, event_factory


@event_factory
def PipelineDispatchJournalWritten(  # noqa: N802
    execution_id: str,
    status: str,
    bytes_written: int,
) -> Event:
    """Emitted when a terminal dispatch record is persisted to sqlite."""
    return Event(
        signal="pipeline.dispatch.journal.written",
        payload={
            "execution_id": execution_id,
            "status": status,
            "bytes": bytes_written,
        },
        scope="node",
    )


@event_factory
def PipelineDispatchJournalRead(  # noqa: N802
    execution_id: str,
    age_seconds: float,
) -> Event:
    """Emitted when tracker fallback serves a terminal record from sqlite."""
    return Event(
        signal="pipeline.dispatch.journal.read",
        payload={
            "execution_id": execution_id,
            "age_seconds": age_seconds,
        },
        scope="node",
    )


@event_factory
def PipelineDispatchJournalPruned(  # noqa: N802
    records_deleted: int,
    oldest_deleted_age_seconds: float | None,
) -> Event:
    """Emitted once per prune round for dispatch journal retention."""
    return Event(
        signal="pipeline.dispatch.journal.pruned",
        payload={
            "records_deleted": records_deleted,
            "oldest_deleted_age_seconds": oldest_deleted_age_seconds,
        },
        scope="node",
    )
