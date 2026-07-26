"""Cold-start seed: replay recent history into the Model before live subscribe."""

from __future__ import annotations

import time
from collections.abc import Callable

from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import (
    charter_tick_audit,
    signal_events,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.lease_snapshot import fetch_lease_snapshot
from scripts.model_manager.ui.dispatch_monitor.ulg.records import event_from_row
from scripts.model_manager.ui.dispatch_monitor.ulg.snapshot_events import (
    events_from_lease_snapshot,
    fold_status_failure_event,
)

_LIVE_FILTERS: tuple[str, ...] = (
    "manage.charter.tick.*",
    "frontier.sdk.*",
    "cdp.generate.*",
    "frontier.poll.hint.issued",
)


def _rows_to_events(rows: list[dict]) -> list[EventRecord]:
    events: list[EventRecord] = []
    for row in rows:
        event = event_from_row(row)
        if event is not None:
            events.append(event)
    return events


def _audit_rows(audit: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ("admitted", "closed", "failed", "waiting_open"):
        chunk = audit.get(key)
        if isinstance(chunk, list):
            rows.extend(chunk)
    return rows


def _lease_snapshot_events(
    *,
    base_url: str | None = None,
    source_repo: str | None = None,
    fetch: Callable[..., dict | None] | None = None,
) -> list[EventRecord]:
    # Lease-snapshot cold-start reconcile — delete when GS1/GS3/GS4 land
    # (project-home §5 row 1; existing-hooks.md admin reconcile).
    snapshot = fetch_lease_snapshot(
        base_url=base_url,
        source_repo=source_repo,
        get_json=fetch,
    )
    if snapshot is None:
        return [fold_status_failure_event(ts_unix_ms=int(time.time() * 1000))]
    return events_from_lease_snapshot(snapshot)


def seed_model(
    apply: Callable[[EventRecord], None],
    *,
    minutes: int = 60,
    limit: int = 500,
    lease_snapshot_url: str | None = None,
    source_repo: str | None = None,
    lease_snapshot_fetch: Callable[..., dict | None] | None = None,
) -> int:
    """Fold cold-start history into ``apply``. Returns count of records applied."""
    seen_seq: set[int] = set()
    pending: list[EventRecord] = []

    audit = charter_tick_audit(minutes=minutes, limit=limit)
    for row in _audit_rows(audit):
        event = event_from_row(row)
        if event is None:
            continue
        if event.seq is not None:
            if event.seq in seen_seq:
                continue
            seen_seq.add(event.seq)
        pending.append(event)

    for pattern in _LIVE_FILTERS:
        for row in signal_events(pattern, minutes=minutes, limit=limit):
            event = event_from_row(row)
            if event is None:
                continue
            if event.seq is not None:
                if event.seq in seen_seq:
                    continue
                seen_seq.add(event.seq)
            pending.append(event)

    pending.extend(
        _lease_snapshot_events(
            base_url=lease_snapshot_url,
            source_repo=source_repo,
            fetch=lease_snapshot_fetch,
        )
    )

    pending.sort(key=lambda item: (item.seq if item.seq is not None else 0, item.ts_unix_ms))
    for event in pending:
        apply(event)
    return len(pending)
