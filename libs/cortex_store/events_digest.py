"""Digest lifecycle events — cortex.digest.* namespace."""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory

from .event_publisher import record


@event_factory
def digest_run(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    session_id: str | None = None,
) -> Event:
    ev = Event(
        signal="cortex.digest.run",
        role="observation",
        scope="global",
        payload={
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "session_id": session_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_extract(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    claim_count: int,
) -> Event:
    ev = Event(
        signal="cortex.digest.extract",
        role="observation",
        scope="global",
        payload={
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "claim_count": claim_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_verify(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    claim_count: int,
) -> Event:
    ev = Event(
        signal="cortex.digest.verify",
        role="observation",
        scope="global",
        payload={
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "claim_count": claim_count,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_staged(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    status: str,
    ledger_id: int | None = None,
    staging_batch_id: str | None = None,
) -> Event:
    ev = Event(
        signal="cortex.digest.staged",
        role="observation",
        scope="global",
        payload={
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "status": status,
            "ledger_id": ledger_id,
            "staging_batch_id": staging_batch_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_enqueued(
    *,
    job_id: str,
    journal_entity_id: str,
    entry_anchor: str,
    kind: str,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.enqueued",
        role="observation",
        scope="global",
        payload={
            "job_id": job_id,
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "kind": kind,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_submitted(
    *,
    job_id: str,
    execution_id: str,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.submitted",
        role="observation",
        scope="global",
        payload={"job_id": job_id, "execution_id": execution_id},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_harvested(
    *,
    job_id: str,
    archive_uri: str,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.harvested",
        role="observation",
        scope="global",
        payload={"job_id": job_id, "archive_uri": archive_uri},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_parsed(
    *,
    job_id: str,
    claim_count: int,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.parsed",
        role="observation",
        scope="global",
        payload={"job_id": job_id, "claim_count": claim_count},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_verified(
    *,
    job_id: str,
    claim_count: int,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.verified",
        role="observation",
        scope="global",
        payload={"job_id": job_id, "claim_count": claim_count},
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_staged(
    *,
    job_id: str,
    ledger_id: int,
    staging_batch_id: str | None = None,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.staged",
        role="observation",
        scope="global",
        payload={
            "job_id": job_id,
            "ledger_id": ledger_id,
            "staging_batch_id": staging_batch_id,
        },
    )
    record(ev.signal, **ev.payload)
    return ev


@event_factory
def digest_job_parked(
    *,
    job_id: str,
    journal_entity_id: str,
    entry_anchor: str,
    reason: str,
) -> Event:
    ev = Event(
        signal="cortex.digest.job.parked",
        role="observation",
        scope="global",
        payload={
            "job_id": job_id,
            "journal_entity_id": journal_entity_id,
            "entry_anchor": entry_anchor,
            "reason": reason,
        },
    )
    record(ev.signal, **ev.payload)
    return ev
