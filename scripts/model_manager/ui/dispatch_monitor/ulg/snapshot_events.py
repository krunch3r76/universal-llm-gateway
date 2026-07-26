"""Convert admin lease-snapshot JSON into core EventRecords for Model.apply."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event
from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord

_RECONCILED_SOURCE = "ulg://git-integration-worker/reconciled"


def _parse_ts_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            return int(datetime.fromisoformat(normalized).timestamp() * 1000)
        except ValueError:
            return None
    return None


def _holder_status_state(status: Any) -> str:
    text = str(status or "running").lower()
    if text in ("admitted", "running", "queued", "parked_waiting"):
        return "running" if text != "queued" else "running"
    return "running"


def events_from_lease_snapshot(
    snapshot: Mapping[str, Any],
    *,
    ts_unix_ms: int | None = None,
) -> list[EventRecord]:
    """Map one lease-snapshot body to EventRecords consumed by Model.apply."""
    stamp = ts_unix_ms
    if stamp is None:
        stamp = _parse_ts_ms(snapshot.get("holder_started_at")) or 0

    events: list[EventRecord] = [
        Event(
            signal=signals.MONITOR_SEED_FOLD_STATUS,
            ts_unix_ms=stamp,
            payload={"fold_status": "seeded", "source": "lease_snapshot"},
            source=_RECONCILED_SOURCE,
        ),
        Event(
            signal=signals.CHARTER_SCANNED,
            ts_unix_ms=stamp,
            payload={
                "queue_depth": int(snapshot.get("queue_depth") or 0),
                "lease_holder": snapshot.get("holder_dispatch_id")
                or snapshot.get("holder_thread_id"),
                signals.PROVENANCE_RECONCILED_KEY: signals.PROVENANCE_RECONCILED,
            },
            source=_RECONCILED_SOURCE,
        ),
    ]

    dispatch_id = snapshot.get("holder_dispatch_id")
    if isinstance(dispatch_id, str) and dispatch_id:
        started_ms = _parse_ts_ms(snapshot.get("holder_started_at")) or stamp
        events.append(
            Event(
                signal=signals.SDK_WORKER_STARTED,
                ts_unix_ms=started_ms,
                payload={
                    "execution_id": dispatch_id,
                    "dispatch_id": dispatch_id,
                    "thread_id": snapshot.get("holder_thread_id"),
                    "model": snapshot.get("holder_resolved_model"),
                    "status": _holder_status_state(snapshot.get("holder_status")),
                    "source_repo": snapshot.get("holder_source_repo"),
                    signals.PROVENANCE_RECONCILED_KEY: signals.PROVENANCE_RECONCILED,
                },
                source=_RECONCILED_SOURCE,
                subject=(
                    str(snapshot["holder_thread_id"])
                    if snapshot.get("holder_thread_id")
                    else dispatch_id
                ),
            )
        )
    return events


def fold_status_failure_event(
    *,
    ts_unix_ms: int = 0,
    reason: str = "lease_snapshot_fetch_failed",
) -> EventRecord:
    """Non-fatal seed failure — monitor starts with fold_status suspect."""
    return Event(
        signal=signals.MONITOR_SEED_FOLD_STATUS,
        ts_unix_ms=ts_unix_ms,
        payload={
            "fold_status": "suspect",
            "source": "lease_snapshot",
            "reason": reason,
        },
        source=_RECONCILED_SOURCE,
    )
