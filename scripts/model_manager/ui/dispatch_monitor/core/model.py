"""Model -- fold in, derive out, zero I/O.

The load-bearing invariant of the whole arc (Fable G3 §3.1):

    ``derive`` is a pure function of ``(folded_state, now_ms)``. Any datum the View
    needs that the Controller obtained by I/O **must enter the Model as an
    EventRecord.** The Controller never derives; the Model never does I/O.

That single rule is what makes the fixture suite total coverage of derivation
rather than partial, and it is why this module imports nothing but the stdlib and
its siblings. No socket, no clock, no file, no bus. Time arrives as the ``now_ms``
argument; everything else arrives through :meth:`Model.apply`.

``apply`` is total over signals: an unrecognised signal is counted in
``health.unhandled_signals`` and never raises. A monitor that crashes on schema
drift is worse than one that reports it, and reporting it is what makes drift
visible on the same surface the operator is already watching.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping

from . import fingerprint as fingerprint_mod
from . import signals
from .attention import derive_attention
from .attention_transport import transport_truncation_items
from .correlation import CorrelationIndex
from .dtos import (
    SCHEMA_VERSION,
    CharterRootRow,
    HealthProjection,
    SupervisorProjection,
    Thresholds,
    severity_rank,
)
from .folds import CdpFold, CharterFold, SdkFold
from .projections import age as _age
from .projections import cdp_rows, root_rows, sdk_rows
from .protocols import EventRecord


class Model:
    """Folded dispatch state plus a pure derivation to an immutable frame."""

    def __init__(self, thresholds: Thresholds | None = None) -> None:
        self.thresholds = thresholds or Thresholds()
        self.index = CorrelationIndex()
        self.charter = CharterFold(self.index)
        self.sdk = SdkFold(self.index)
        self.cdp = CdpFold(self.index)
        self.records_folded = 0
        self.seq_high_water: int | None = None
        self.dropped_ingest = 0
        self.dropped_subscribe = 0
        self.unhandled: dict[str, int] = {}
        self.fold_status = "live"
        self.reconcile_failures: dict[str, tuple[str, str, str]] = {}
        self.replay_truncations: dict[str, tuple[int | None, str, int | None]] = {}
        self._handlers: dict[str, Callable[[EventRecord], None]] = {}
        self._handlers.update(self.charter.handlers())
        self._handlers.update(self.sdk.handlers())
        self._handlers.update(self.cdp.handlers())
        self._handlers[signals.EVENTS_DROPPED_INGEST] = self._on_dropped_ingest
        self._handlers[signals.EVENTS_DROPPED_SUBSCRIBE] = self._on_dropped_subscribe
        self._handlers[signals.MONITOR_SEED_FOLD_STATUS] = self._on_fold_status
        self._handlers[signals.MONITOR_RECONCILE_SOURCE_FAILED] = (
            self._on_reconcile_source_failed
        )
        self._handlers[signals.MONITOR_TRANSPORT_REPLAY_TRUNCATED] = (
            self._on_replay_truncated
        )

    @property
    def handled_signals(self) -> tuple[str, ...]:
        """Return every signal the handler table covers, sorted."""
        return tuple(sorted(self._handlers))

    # --- fold -------------------------------------------------------------
    def apply(self, record: EventRecord) -> None:
        """Fold one record into state. Total over signals; never raises on content."""
        self.records_folded += 1
        seq = getattr(record, "seq", None)
        if isinstance(seq, int) and (
            self.seq_high_water is None or seq > self.seq_high_water
        ):
            self.seq_high_water = seq
        handler = self._handlers.get(record.signal)
        if handler is None:
            self.unhandled[record.signal] = self.unhandled.get(record.signal, 0) + 1
            return
        handler(record)

    def apply_all(self, records: Any) -> None:
        """Fold an iterable of records in order."""
        for record in records:
            self.apply(record)

    def _on_dropped_ingest(self, record: EventRecord) -> None:
        """Count lost fold inputs. These mean folded state may be incomplete."""
        self.dropped_ingest += _count(record.payload)

    def _on_dropped_subscribe(self, record: EventRecord) -> None:
        """Count View-side drops. Correct behaviour under overload, not an error."""
        self.dropped_subscribe += _count(record.payload)

    def _on_fold_status(self, record: EventRecord) -> None:
        """Fold cold-start seed posture from graft-only meta events."""
        status = record.payload.get("fold_status")
        if isinstance(status, str) and status:
            self.fold_status = status

    def _on_reconcile_source_failed(self, record: EventRecord) -> None:
        """Remember click-time reconcile source failures for attention projection."""
        payload = record.payload
        subject = payload.get("subject")
        source = payload.get("source")
        error = payload.get("error")
        if not all(isinstance(value, str) and value for value in (subject, source, error)):
            return
        key = f"monitor.reconcile.failed:{subject}:{source}"
        self.reconcile_failures[key] = (subject, source, error)

    def _on_replay_truncated(self, record: EventRecord) -> None:
        """Remember GX1 truncation per subscribe connection for attention projection."""
        payload = record.payload
        connection = payload.get("connection")
        reason = payload.get("reason")
        if not isinstance(connection, str) or not connection:
            return
        if not isinstance(reason, str) or not reason:
            reason = "unknown"
        requested = payload.get("requested_seq")
        first_seq = payload.get("first_seq")
        req = requested if isinstance(requested, int) and not isinstance(requested, bool) else None
        first = first_seq if isinstance(first_seq, int) and not isinstance(first_seq, bool) else None
        self.replay_truncations[connection] = (req, reason, first)

    # --- derive -----------------------------------------------------------
    def derive(
        self, now_ms: int, previous: SupervisorProjection | None = None
    ) -> SupervisorProjection:
        """Return an immutable frame for ``now_ms``. Pure: no I/O, no hidden clock.

        ``previous`` is optional and used only to compute advisory
        ``changed_hints``. Passing it keeps the function pure -- the Controller owns
        the previous frame, the Model does not remember it -- and omitting it is
        valid: ``derive(now_ms)`` yields hints of ``()``.
        """
        dispatches = sdk_rows(self.sdk, self.index, now_ms)
        legs = cdp_rows(self.cdp, self.index, now_ms)
        roots = root_rows(self.charter, dispatches, self.thresholds, now_ms)
        health = self._health(now_ms, roots)
        attention = derive_attention(
            health=health,
            roots=roots,
            dispatches=dispatches,
            legs=legs,
            thresholds=self.thresholds,
            reconcile_failures=self.reconcile_failures,
        )
        if self.replay_truncations:
            merged = (*attention, *transport_truncation_items(self.replay_truncations))
            attention = tuple(
                sorted(
                    merged,
                    key=lambda i: (-severity_rank(i.severity), i.kind, i.subject, i.key),
                )
            )
        frame = SupervisorProjection(
            schema_version=SCHEMA_VERSION,
            generated_at_ms=now_ms,
            fingerprint="",
            health=health,
            roots=roots,
            sdk=dispatches,
            cdp=legs,
            attention=attention,
            arcs={},
            changed_hints=(),
        )
        sections = fingerprint_mod.pruned_sections(frame)
        frame = replace(
            frame, fingerprint=fingerprint_mod.compute_from_sections(sections)
        )
        return replace(frame, changed_hints=_hints(previous, sections))

    def _health(
        self, now_ms: int, roots: tuple[CharterRootRow, ...]
    ) -> HealthProjection:
        """Project global health, including the monitor's own drift counters."""
        degraded: list[str] = []
        if self.dropped_ingest:
            degraded.append("fold_inputs_dropped")
        if self.unhandled:
            degraded.append("unhandled_signals")
        if self.charter.last_error_ms is not None:
            degraded.append("charter_tick_error")
        in_flight = sum(1 for r in roots if r.state in ("in_flight", "waiting_open"))
        return HealthProjection(
            tick_last_scan_ms=self.charter.last_scan_ms,
            tick_last_scan_age_ms=_age(now_ms, self.charter.last_scan_ms),
            tick_roots_scanned=self.charter.roots_scanned,
            tick_admitted_last_scan=self.charter.admitted_last_scan,
            tick_admitted_total=self.charter.admitted_total,
            tick_last_error_ms=self.charter.last_error_ms,
            tick_last_error_message=self.charter.last_error_message,
            skipped_by_reason=dict(sorted(self.charter.skipped_by_reason.items())),
            lease_holder=self.charter.lease_holder,
            lease_expires_ms=self.charter.lease_expires_ms,
            queue_depth=self.charter.queue_depth,
            wip_capacity=self.charter.wip_capacity,
            wip_in_use=self.charter.wip_in_use or in_flight,
            events_dropped_ingest=self.dropped_ingest,
            events_dropped_subscribe=self.dropped_subscribe,
            records_folded=self.records_folded,
            unhandled_signals=dict(sorted(self.unhandled.items())),
            seq_high_water=self.seq_high_water,
            cold_start_seeded=self.charter.cold_start_seeded,
            fold_status=self.fold_status,
            charter_loop_state=self.charter.loop_state,
            charter_last_reload_ms=self.charter.last_reload_ms,
            charter_reload_module_count=self.charter.reload_module_count,
            degraded=tuple(degraded),
        )


def _count(payload: Mapping[str, Any]) -> int:
    """Extract a drop count from a drop-event payload, defaulting to one."""
    for key in ("count", "dropped", "n"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return 1


#: Frame sections a hint may name, in render order.
HINT_SECTIONS = ("health", "roots", "sdk", "cdp", "attention", "arcs")


def _hints(
    previous: SupervisorProjection | None, sections: Mapping[str, Any]
) -> tuple[str, ...]:
    """Return advisory ``changed_hints`` for ``sections`` relative to ``previous``.

    Compared against the *pruned* section view, the same one the fingerprint hashes.
    Comparing raw DTOs instead would name ``health`` on every tick, because its age
    counters advance whether or not anything happened -- a hint that fires always
    is a hint that says nothing.

    Advisory means advisory. Under drop-oldest broadcast a subscriber that lost
    frames cannot trust these, so the Controller must stamp ``("*",)`` on the first
    delivery after any drop -- see :func:`hints_after_drop`. A View that treats
    hints as authoritative renders stale panels after an overflow, silently, and
    only under load.
    """
    if previous is None:
        return ()
    before = fingerprint_mod.pruned_sections(previous)
    return tuple(
        name for name in HINT_SECTIONS if before.get(name) != sections.get(name)
    )


def hints_after_drop(projection: SupervisorProjection) -> SupervisorProjection:
    """Return ``projection`` with ``changed_hints`` forced to ``("*",)``.

    The Controller calls this for the first frame delivered to a subscriber that
    just lost frames. Provided here so the rule lives beside the hint computation
    it corrects, rather than as folklore in the graft.
    """
    return replace(projection, changed_hints=("*",))
