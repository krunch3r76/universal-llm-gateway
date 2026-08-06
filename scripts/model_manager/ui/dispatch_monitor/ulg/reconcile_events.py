"""Map reconcile source payloads into core EventRecords for Model.apply."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.model_manager.ui.charter_scoreboard_objective import (
    parse_original_objective,
)
from scripts.model_manager.ui.dispatch_monitor.core import signals
from scripts.model_manager.ui.dispatch_monitor.core.protocols import Event, EventRecord
from scripts.model_manager.ui.dispatch_monitor.ulg.records import event_from_row

_RECONCILED_SOURCE = "ulg://dispatch-monitor/reconciled"

_ES_WORKER_TERMINAL_SIGNALS = frozenset(
    {
        signals.SDK_WORKER_COMPLETED,
        signals.SDK_WORKER_FAILED,
        signals.SDK_WORKER_TIMEOUT,
        signals.SDK_WORKER_ORPHANED,
        signals.SDK_WORKER_CANCELLED,
    }
)


def _with_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(payload)
    merged[signals.PROVENANCE_RECONCILED_KEY] = signals.PROVENANCE_RECONCILED
    return merged


def source_failure_event(
    *,
    subject: str,
    source: str,
    error: str,
    ts_unix_ms: int,
) -> EventRecord:
    """Non-fatal reconcile source failure — surfaces via attention derivation."""
    return Event(
        signal=signals.MONITOR_RECONCILE_SOURCE_FAILED,
        ts_unix_ms=ts_unix_ms,
        payload={
            "subject": subject,
            "source": source,
            "error": error,
        },
        source=_RECONCILED_SOURCE,
        subject=subject,
    )


def events_from_bus(
    data: Mapping[str, Any],
    *,
    subject: str,
    ts_unix_ms: int,
) -> list[EventRecord]:
    """Fold agent-bus thread metadata into charter / correlation events."""
    events: list[EventRecord] = []
    thread_id = str(data.get("id") or subject)
    links = data.get("dispatch_links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                continue
            execution_id = link.get("execution_id")
            if not execution_id:
                continue
            events.append(
                Event(
                    signal=signals.MONITOR_META_SDK_STARTED,
                    ts_unix_ms=ts_unix_ms,
                    payload=_with_provenance(
                        {
                            "execution_id": str(execution_id),
                            "dispatch_id": str(execution_id),
                            "thread_id": thread_id,
                            "status": link.get("terminal_status") or "running",
                            "pipeline_id": link.get("pipeline_id"),
                        }
                    ),
                    source=_RECONCILED_SOURCE,
                    subject=thread_id,
                )
            )
    status = data.get("bus_lifecycle_state")
    if isinstance(status, str) and status:
        events.append(
            Event(
                signal=signals.CHARTER_WAITING_OPEN,
                ts_unix_ms=ts_unix_ms,
                payload=_with_provenance(
                    {"root": thread_id, "age_s": 0, "lifecycle": status}
                ),
                source=_RECONCILED_SOURCE,
                subject=thread_id,
            )
        )
    return events


def _ledger_status_signal(status: Any) -> str | None:
    text = str(status or "").lower()
    if text in ("queued", "admitted", "running", "parked_waiting"):
        return signals.MONITOR_META_SDK_STARTED
    if text == "completed":
        return signals.SDK_WORKER_COMPLETED
    if text == "failed":
        return signals.SDK_WORKER_FAILED
    return None


def events_from_ledger(
    data: Mapping[str, Any],
    *,
    subject: str,
    ts_unix_ms: int,
) -> list[EventRecord]:
    """Fold dispatch-status ledger row into SDK events."""
    status = data.get("status")
    if status is None:
        return [
            source_failure_event(
                subject=subject,
                source="ledger",
                error="no_dispatch_row",
                ts_unix_ms=ts_unix_ms,
            )
        ]
    signal = _ledger_status_signal(status)
    if signal is None:
        return [
            source_failure_event(
                subject=subject,
                source="ledger",
                error=f"unknown_status:{status}",
                ts_unix_ms=ts_unix_ms,
            )
        ]
    dispatch_id = data.get("dispatch_id") or data.get("execution_id") or subject
    payload: dict[str, Any] = {
        "execution_id": str(dispatch_id),
        "dispatch_id": str(dispatch_id),
        "thread_id": data.get("thread_id") or subject,
        "status": str(status),
    }
    if signal == signals.SDK_WORKER_COMPLETED:
        payload["outcome"] = "completed"
    if signal == signals.SDK_WORKER_FAILED:
        payload["error"] = "reconciled_terminal"
    return [
        Event(
            signal=signal,
            ts_unix_ms=ts_unix_ms,
            payload=_with_provenance(payload),
            source=_RECONCILED_SOURCE,
            subject=str(payload.get("thread_id") or subject),
        )
    ]


def events_from_es_worker_terminals(
    rows: list[Mapping[str, Any]],
    *,
    dispatch_id: str,
) -> list[EventRecord]:
    """Synthesize foldable worker terminals from ES rows (G4b backfill)."""
    events: list[EventRecord] = []
    for row in rows:
        event = event_from_row(row)
        if event is None or event.signal not in _ES_WORKER_TERMINAL_SIGNALS:
            continue
        payload = _with_provenance(dict(event.payload))
        if not payload.get("dispatch_id"):
            payload["dispatch_id"] = dispatch_id
        if not payload.get("execution_id"):
            payload["execution_id"] = dispatch_id
        events.append(
            Event(
                signal=event.signal,
                ts_unix_ms=event.ts_unix_ms,
                payload=payload,
                source=_RECONCILED_SOURCE,
                subject=event.subject or dispatch_id,
                seq=event.seq,
            )
        )
    return events


def events_from_cortex(
    data: Mapping[str, Any],
    *,
    subject: str,
    ts_unix_ms: int,
) -> list[EventRecord]:
    """Record scoreboard presence and graft objective when the section exists."""
    uri = data.get("uri")
    line_count = data.get("line_count")
    events: list[EventRecord] = [
        Event(
            signal=signals.CHARTER_SCANNED,
            ts_unix_ms=ts_unix_ms,
            payload=_with_provenance(
                {
                    "roots": 1,
                    "admitted": 0,
                    "scoreboard_uri": uri,
                    "scoreboard_lines": line_count,
                    "reconcile_subject": subject,
                }
            ),
            source=_RECONCILED_SOURCE,
            subject=subject,
        )
    ]
    content = data.get("content")
    if isinstance(content, str):
        objective = parse_original_objective(content)
        if objective:
            events.append(
                Event(
                    signal=signals.MONITOR_META_CHARTER_OBJECTIVE,
                    ts_unix_ms=ts_unix_ms,
                    payload=_with_provenance(
                        {"root": subject, "objective": objective}
                    ),
                    source=_RECONCILED_SOURCE,
                    subject=subject,
                )
            )
    return events
