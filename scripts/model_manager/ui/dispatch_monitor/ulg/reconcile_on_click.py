"""Click-time reconcile adapter — bus, ledger, cortex on explicit operator trigger."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Mapping

from scripts.model_manager.ui.dispatch_monitor.core.protocols import EventRecord
from scripts.model_manager.ui.dispatch_monitor.core.protocols import ReconcilePort
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_events import (
    events_from_bus,
    events_from_cortex,
    events_from_ledger,
    source_failure_event,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.reconcile_sources import (
    SourceOutcome,
    fetch_bus_thread,
    fetch_cortex_scoreboard,
    fetch_ledger_dispatch,
)
from scripts.model_manager.ui.dispatch_monitor.ulg.subject_ref import resolve_thread_id


class ReconcileOnClick:
    """Operator-initiated reconcile against bus, ledger, and cortex."""

    def __init__(
        self,
        *,
        bus_fetch: Callable[[str], SourceOutcome] | None = None,
        ledger_fetch: Callable[[str], SourceOutcome] | None = None,
        cortex_fetch: Callable[[str, dict[str, Any] | None], SourceOutcome]
        | None = None,
        bus_lookup: Callable[[str], dict[str, Any] | None] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._bus_fetch = bus_fetch or fetch_bus_thread
        self._ledger_fetch = ledger_fetch or fetch_ledger_dispatch
        self._cortex_fetch = cortex_fetch
        self._bus_lookup = bus_lookup
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def reconcile(self, kind: str, key: str) -> Mapping[str, Any]:
        """Satisfy :class:`~dispatch_monitor.core.protocols.ReconcilePort`."""
        if kind != "subject":
            return {"error": "unsupported_kind", "kind": kind}
        events, outcomes = self.reconcile_subject(key)
        return {
            "subject": key,
            "events_applied": len(events),
            "sources": [asdict(item) for item in outcomes],
        }

    def reconcile_subject(
        self, subject: str
    ) -> tuple[list[EventRecord], list[SourceOutcome]]:
        """Fetch three sources independently; failures never abort siblings."""
        ts = self._now_ms()
        thread_id = resolve_thread_id(subject, bus_get=self._bus_lookup)
        scope = thread_id or subject
        outcomes: list[SourceOutcome] = []
        events: list[EventRecord] = []

        bus = self._bus_fetch(scope)
        outcomes.append(bus)
        if bus.ok and bus.data:
            events.extend(
                events_from_bus(bus.data, subject=subject, ts_unix_ms=ts)
            )
        elif not bus.ok:
            events.append(
                source_failure_event(
                    subject=subject,
                    source="bus",
                    error=bus.error or "bus_fetch_failed",
                    ts_unix_ms=ts,
                )
            )

        ledger = self._ledger_fetch(scope)
        outcomes.append(ledger)
        if ledger.ok and ledger.data:
            events.extend(
                events_from_ledger(ledger.data, subject=subject, ts_unix_ms=ts)
            )
        elif not ledger.ok:
            events.append(
                source_failure_event(
                    subject=subject,
                    source="ledger",
                    error=ledger.error or "ledger_fetch_failed",
                    ts_unix_ms=ts,
                )
            )

        cortex_fn = self._cortex_fetch or (
            lambda tid, bus_data: fetch_cortex_scoreboard(tid, bus_data=bus_data)
        )
        cortex = cortex_fn(scope, bus.data if bus.ok else None)
        outcomes.append(cortex)
        if cortex.ok and cortex.data:
            events.extend(
                events_from_cortex(cortex.data, subject=subject, ts_unix_ms=ts)
            )
        elif not cortex.ok:
            events.append(
                source_failure_event(
                    subject=subject,
                    source="cortex",
                    error=cortex.error or "cortex_fetch_failed",
                    ts_unix_ms=ts,
                )
            )

        return events, outcomes


def default_reconcile_port() -> ReconcilePort:
    """Factory for the live graft reconcile adapter."""
    return ReconcileOnClick()
