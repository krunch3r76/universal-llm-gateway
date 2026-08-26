"""Cold-start seed: replay recent history into the Model before live subscribe."""

from __future__ import annotations

import time
from collections.abc import Callable

from scripts.model_manager.ui.charter_scoreboard_objective import (
    objective_meta_event,
    tip_meta_for_root,
)
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event, EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.event_query import (
    charter_tick_audit,
    signal_events,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.lease_snapshot import (
    fetch_lease_snapshot,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.records import event_from_row
from scripts.model_manager.ui.dispatch_monitor.ulg.snapshot_events import (
    events_from_lease_snapshot,
    fold_status_failure_event,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.terminal_backfill import (
    backfill_sdk_fold,
)

_LIVE_FILTERS: tuple[str, ...] = (
    "manage.charter.tick.*",
    "frontier.sdk.*",
    "mcp.team.dispatch.dispatched",
    "cdp.generate.*",
    "frontier.poll.hint.issued",
    "system.started",
)

#: Exact pulls that must survive glob crowding (shadow.diff / toolcall flood the
#: 500-cap window and starve lifecycle terminals + root rows).
_PRIORITY_SIGNALS: tuple[str, ...] = (
    "manage.charter.tick.admitted",
    "manage.charter.tick.closed",
    "manage.charter.tick.root_skipped",
    "manage.charter.tick.root_closed",
    "manage.charter.tick.window_failed",
    "manage.charter.tick.waiting_open",
    "manage.charter.tick.scanned",
    "manage.charter.tick.paused",
    "manage.charter.tick.held",
    "manage.charter.tick.resumed",
    "frontier.sdk.worker.completed",
    "frontier.sdk.worker.failed",
    "frontier.sdk.worker.queued",
    "frontier.sdk.worker.timeout",
    "frontier.sdk.worker.orphaned",
    "frontier.sdk.worker.cancelled",
    "frontier.sdk.worker.progress",
    "frontier.sdk.generate.requested",
    "frontier.sdk.worker.lease.acquired",
    "frontier.sdk.worker.lease.promoted",
    "frontier.sdk.worker.lease.released",
    "frontier.sdk.worker.lease.park_enter",
    "frontier.sdk.worker.lease.park_restore",
    "frontier.sdk.closeout.partial_work.production_specimen",
    "frontier.sdk.closeout.relayed",
    "pipeline.frontier.dispatch.started",
    "pipeline.frontier.dispatch.completed",
    "pipeline.frontier.dispatch.failed",
    "frontier.sdk.review_child.spawned",
    "mcp.team.dispatch.dispatched",
    "system.started",
)

#: Signals whose payload names a charter root worth tip-grafting.
_ROOT_TIP_SIGNALS = frozenset(
    {
        signals.CHARTER_ADMITTED,
        signals.CHARTER_CLOSED,
        signals.CHARTER_ROOT_SKIPPED,
        signals.CHARTER_ROOT_CLOSED,
        signals.CHARTER_WAITING_OPEN,
        signals.CHARTER_WINDOW_FAILED,
        signals.CHARTER_TRANSITION,
        signals.CHARTER_CONSULT_QUEUED,
        signals.CHARTER_CONSULT_DEFERRED,
        signals.CHARTER_ENROLLMENT_FILTERED,
        signals.CHARTER_FRICTIONS_AUDIT_PASSED,
    }
)


def _seed_noise(signal: str) -> bool:
    """Drop high-volume signals that never fold and crowd out lifecycle events."""
    if ".shadow." in signal:
        return True
    if signal.endswith(".toolcall"):
        return True
    return False


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


def _payload_root_id(payload: dict, subject: str | None) -> str | None:
    for key in ("root", "root_id", "root_thread", "thread"):
        value = payload.get(key)
        if value:
            return str(value)
    if subject:
        return str(subject)
    return None


def _graft_charter_tips(
    apply: Callable[[EventRecord], None],
    root_ids: set[str],
) -> int:
    """Cold-start graft of ledger tip + bus slug/summary identity."""
    if not root_ids:
        return 0
    ts = int(time.time() * 1000)
    count = 0
    for root_id in sorted(root_ids):
        tip = tip_meta_for_root(root_id)
        if not tip.has_identity:
            continue
        apply(
            Event(
                signal=signals.MONITOR_META_CHARTER_OBJECTIVE,
                ts_unix_ms=ts,
                payload=objective_meta_event(
                    root_id,
                    tip.objective,
                    pickup_gid=tip.pickup_gid,
                    scoreboard_uri=tip.scoreboard_uri,
                    bus_slug=tip.bus_slug,
                    bus_summary=tip.bus_summary,
                    ts_unix_ms=ts,
                ),
                source="ulg://dispatch-monitor/seed",
                subject=root_id,
            )
        )
        count += 1
    return count


def seed_model(
    apply: Callable[[EventRecord], None],
    *,
    minutes: int = 60,
    limit: int = 500,
    lease_snapshot_url: str | None = None,
    source_repo: str | None = None,
    lease_snapshot_fetch: Callable[..., dict | None] | None = None,
    sdk_fold: object | None = None,
    backfill_minutes: int | None = None,
) -> int:
    """Fold cold-start history into ``apply``. Returns count of records applied."""
    seen_seq: set[int] = set()
    pending: list[EventRecord] = []
    seeded_roots: set[str] = set()

    def _ingest(row: dict) -> None:
        event = event_from_row(row)
        if event is None or _seed_noise(event.signal):
            return
        if event.seq is not None:
            if event.seq in seen_seq:
                return
            seen_seq.add(event.seq)
        if event.signal in _ROOT_TIP_SIGNALS:
            root = _payload_root_id(dict(event.payload), event.subject)
            if root:
                seeded_roots.add(root)
        pending.append(event)

    audit = charter_tick_audit(minutes=minutes, limit=limit)
    for row in _audit_rows(audit):
        _ingest(row)

    # Lifecycle first — exact signals beat glob crowding under the 500-cap.
    for signal in _PRIORITY_SIGNALS:
        for row in signal_events(signal, minutes=minutes, limit=limit):
            _ingest(row)

    for pattern in _LIVE_FILTERS:
        for row in signal_events(pattern, minutes=minutes, limit=limit):
            _ingest(row)

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
    _graft_charter_tips(apply, seeded_roots)
    if sdk_fold is not None:
        lookback = backfill_minutes if backfill_minutes is not None else max(minutes, 24 * 60)
        backfill_sdk_fold(apply, sdk_fold, minutes=lookback)
    return len(pending)
