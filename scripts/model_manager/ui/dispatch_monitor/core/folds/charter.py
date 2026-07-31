"""CharterFold -- folds the ``manage.charter.tick.*`` family into root rows.

Negative space, stated because getting it wrong is the likeliest silent defect:

* ``manage.charter.tick.closed`` is **window**-shaped (``window_index`` /
  ``worker_thread`` / ``worker_closed``). It closes a *harvest window*, not a
  root. Only ``root_closed`` closes a root. Cortex records this explicitly --
  overloading ``.closed`` with root-state meaning was considered and killed in
  ``specs/charter-runner-state-close-on-no-gated-pickup.md``.
* ``scanned`` with ``admitted=0`` means the tick decided against admitting. It is
  **not** a health problem: tick health and admission progress are different
  claims, and conflating them is the exact confusion that spec was written for.
* A root that stops appearing in ``scanned`` is not thereby closed. Absence
  carries no disposition; the row keeps its last observed state.
* ``arc_g_step`` is a mirror of ``admitted.path_sim_g_step``. If the payload lacks
  it, it stays ``None`` forever. There is no CHECKPOINT parser here and no other
  route to that value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import signals
from ..correlation import CorrelationIndex
from ..protocols import EventRecord, envelope_subject


#: Roots wedged on consult queue or identical-work refire — must not read as finished.
CONSULT_QUEUED_STUCK_SCAN_THRESHOLD = 3


class RootState:
    """Mutable per-root accumulator. Projected to a frozen row by ``derive``."""

    __slots__ = (
        "root_id",
        "state",
        "project",
        "worker_thread",
        "window_index",
        "admission_mode",
        "packet_path",
        "arc_g_step",
        "arc_g_step_label",
        "pickup_gid",
        "objective",
        "bus_slug",
        "bus_summary",
        "last_signal_ms",
        "last_signal",
        "admitted_at_ms",
        "skip_reason",
        "skip_streak",
        "checkpoint_turn",
        "waiting_open_since_ms",
        "closed",
        "unenrolled",
        "consult_queued_streak",
        "refire_refused_streak",
    )

    def __init__(self, root_id: str) -> None:
        self.root_id = root_id
        self.state = "unknown"
        self.project: str | None = None
        self.worker_thread: str | None = None
        self.window_index: int | None = None
        self.admission_mode: str | None = None
        self.packet_path: str | None = None
        self.arc_g_step: str | None = None
        self.arc_g_step_label: str | None = None
        self.pickup_gid: str | None = None
        self.objective: str | None = None
        self.bus_slug: str | None = None
        self.bus_summary: str | None = None
        self.last_signal_ms: int | None = None
        self.last_signal: str | None = None
        self.admitted_at_ms: int | None = None
        self.skip_reason: str | None = None
        self.skip_streak = 0
        self.checkpoint_turn: int | None = None
        self.waiting_open_since_ms: int | None = None
        self.closed = False
        self.unenrolled = False
        self.consult_queued_streak = 0
        self.refire_refused_streak = 0


def _root_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve the root a charter record is about, preferring envelope subject."""
    for key in ("root", "root_id", "root_thread", "thread"):
        value = payload.get(key)
        if value:
            return str(value)
    return envelope_subject(record)


def _as_int(value: Any) -> int | None:
    """Coerce ``value`` to ``int``, returning ``None`` when it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CharterFold:
    """Accumulates charter-tick state: per-root rows plus global tick health."""

    def __init__(self, index: CorrelationIndex) -> None:
        self._index = index
        self.roots: dict[str, RootState] = {}
        self.last_scan_ms: int | None = None
        self.roots_scanned = 0
        self.admitted_last_scan = 0
        self.admitted_total = 0
        self.skipped_by_reason: dict[str, int] = {}
        self.last_error_ms: int | None = None
        self.last_error_message: str | None = None
        self.lease_holder: str | None = None
        self.lease_expires_ms: int | None = None
        self.queue_depth = 0
        self.wip_capacity: int | None = None
        self.wip_in_use = 0
        self.cold_start_seeded = False
        self.loop_state = "unknown"
        self.last_reload_ms: int | None = None
        self.reload_module_count = 0
        self.hold_active: bool | None = None
        self.hold_reason: str | None = None

    def handlers(self) -> dict[str, Any]:
        """Return this fold's signal-to-handler table."""
        return {
            signals.CHARTER_SCANNED: self._on_scanned,
            signals.CHARTER_ADMITTED: self._on_admitted,
            signals.CHARTER_CLOSED: self._on_window_closed,
            signals.CHARTER_ROOT_SKIPPED: self._on_root_skipped,
            signals.CHARTER_ROOT_CLOSED: self._on_root_closed,
            signals.CHARTER_WAITING_OPEN: self._on_waiting_open,
            signals.CHARTER_ERROR: self._on_error,
            signals.CHARTER_INTENT_HEALED: self._on_intent_healed,
            signals.CHARTER_AUDIT: self._on_audit,
            signals.CHARTER_STARTED: self._on_lifecycle,
            signals.CHARTER_STOPPED: self._on_lifecycle,
            signals.CHARTER_RELOADED: self._on_reload,
            signals.CHARTER_WINDOW_FAILED: self._on_window_failed,
            signals.CHARTER_PAUSED: self._on_hold_armed,
            signals.CHARTER_HELD: self._on_hold_armed,
            signals.CHARTER_RESUMED: self._on_hold_cleared,
            signals.MONITOR_META_CHARTER_OBJECTIVE: self._on_objective,
            signals.CHARTER_FRICTIONS_AUDIT_PASSED: self._on_informational_root,
            signals.CHARTER_TRANSITION: self._on_informational_root,
            signals.CHARTER_CONSULT_QUEUED: self._on_consult_queued,
            signals.CHARTER_CONSULT_DEFERRED: self._on_informational_root,
            signals.CHARTER_IDENTICAL_WORK_REFIRE_REFUSED: (
                self._on_identical_work_refire_refused
            ),
            signals.CHARTER_ENROLLMENT_FILTERED: self._on_informational_root,
            signals.CHARTER_ROOT_BLOCKED: self._on_root_blocked,
            signals.CHARTER_ROOT_UNBLOCKED: self._on_root_unblocked,
            # Shadow path floods every tick for many roots — ack only; never mint rows.
            signals.CHARTER_SHADOW_DIFF: self._on_telemetry_ack,
            signals.CHARTER_SHADOW_STARVED: self._on_telemetry_ack,
        }

    def _root(self, root_id: str, record: EventRecord) -> RootState:
        """Return (creating if needed) the accumulator for ``root_id``.

        ``last_signal_ms`` advances monotonically. A duplicate re-delivered across a
        ``resume_from`` overlap carries an older timestamp, and assigning it would
        rewind the row's clock -- the same rewind class the SDK and CDP folds guard.
        """
        row = self.roots.get(root_id)
        if row is None:
            row = RootState(root_id)
            self.roots[root_id] = row
        if row.last_signal_ms is None or record.ts_unix_ms >= row.last_signal_ms:
            row.last_signal_ms = record.ts_unix_ms
            row.last_signal = record.signal
        return row

    # --- handlers ---------------------------------------------------------
    def _on_scanned(self, record: EventRecord) -> None:
        """Fold the aggregate scan: counts, skip histogram, lease and WIP posture."""
        payload = record.payload
        self.last_scan_ms = record.ts_unix_ms
        # A completed scan means the tick loop imported and ran — supersede any
        # prior tick.error latch (e.g. ImportError storm cleared by quit/start).
        self.last_error_ms = None
        self.last_error_message = None
        self.roots_scanned = _as_int(payload.get("roots")) or 0
        self.admitted_last_scan = _as_int(payload.get("admitted")) or 0
        histogram = payload.get("skipped_by_reason")
        if isinstance(histogram, Mapping):
            self.skipped_by_reason = {
                str(k): _as_int(v) or 0 for k, v in histogram.items()
            }
        for row in self.roots.values():
            if row.state != "consult_queued":
                continue
            row.consult_queued_streak += 1
            if row.consult_queued_streak >= CONSULT_QUEUED_STUCK_SCAN_THRESHOLD:
                row.state = "stuck"
                row.skip_reason = row.skip_reason or "consult_queued_streak"
        for key in ("lease_holder", "holder"):
            if payload.get(key):
                self.lease_holder = str(payload[key])
                break
        if _as_int(payload.get("lease_expires_ms")) is not None:
            self.lease_expires_ms = _as_int(payload.get("lease_expires_ms"))
        self.queue_depth = _as_int(payload.get("queue_depth")) or self.queue_depth
        if _as_int(payload.get("wip_capacity")) is not None:
            self.wip_capacity = _as_int(payload.get("wip_capacity"))
        self.wip_in_use = _as_int(payload.get("wip_in_use")) or self.wip_in_use

    def _on_admitted(self, record: EventRecord) -> None:
        """Mark a root in flight and link its worker thread into the index."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        row.state = "in_flight"
        row.admitted_at_ms = record.ts_unix_ms
        row.skip_reason = None
        row.skip_streak = 0
        row.consult_queued_streak = 0
        row.refire_refused_streak = 0
        row.waiting_open_since_ms = None
        row.closed = False
        worker = payload.get("worker_thread")
        if worker:
            row.worker_thread = str(worker)
            self._index.link_root_worker_thread(root_id, str(worker))
        if _as_int(payload.get("window_index")) is not None:
            row.window_index = _as_int(payload.get("window_index"))
        for src, dst in (
            ("admission_mode", "admission_mode"),
            ("packet_path", "packet_path"),
            ("project", "project"),
            ("path_sim_g_step", "arc_g_step"),
            ("path_sim_g_step_label", "arc_g_step_label"),
        ):
            if payload.get(src):
                setattr(row, dst, str(payload[src]))
        self._absorb_objective(row, payload)
        self.admitted_total += 1

    def _on_objective(self, record: EventRecord) -> None:
        """Graft scoreboard / ledger tip / bus identity onto a root."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self.roots.get(root_id)
        if row is None:
            return
        if row.last_signal_ms is None or record.ts_unix_ms >= row.last_signal_ms:
            row.last_signal_ms = record.ts_unix_ms
            row.last_signal = record.signal
        self._absorb_objective(row, payload)
        pickup = payload.get("pickup_gid")
        if pickup:
            row.pickup_gid = str(pickup).strip() or row.pickup_gid
        slug = payload.get("bus_slug")
        if slug:
            row.bus_slug = str(slug).strip() or row.bus_slug
        summary = payload.get("bus_summary")
        if summary:
            row.bus_summary = str(summary).strip() or row.bus_summary

    def _absorb_objective(self, row: RootState, payload: Mapping[str, Any]) -> None:
        for key in ("objective", "charter_objective", "original_objective"):
            value = payload.get(key)
            if value:
                row.objective = str(value).strip()
                return

    def _on_window_closed(self, record: EventRecord) -> None:
        """Fold a harvest-window close. Records the window; does NOT close the root."""
        payload = record.payload
        root_id = _root_id(payload, record) or self._index.root_for_thread(
            payload.get("worker_thread")
        )
        if not root_id:
            return
        row = self._root(root_id, record)
        if _as_int(payload.get("window_index")) is not None:
            row.window_index = _as_int(payload.get("window_index"))
        if _as_int(payload.get("checkpoint_turn")) is not None:
            row.checkpoint_turn = _as_int(payload.get("checkpoint_turn"))
        row.waiting_open_since_ms = None
        if payload.get("worker_closed") is True:
            row.state = "window_closed"

    def _on_root_skipped(self, record: EventRecord) -> None:
        """Record a per-tick skip decision and lengthen the streak."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        reason = payload.get("reason")
        row.skip_reason = str(reason) if reason else None
        row.skip_streak += 1
        if _as_int(payload.get("checkpoint_turn")) is not None:
            row.checkpoint_turn = _as_int(payload.get("checkpoint_turn"))
        if not row.closed:
            row.state = "skipped"

    def _on_root_closed(self, record: EventRecord) -> None:
        """Close a root. The only signal permitted to do so."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        row.state = "closed"
        row.closed = bool(payload.get("closed", True))
        row.unenrolled = bool(payload.get("unenrolled", False))
        reason = payload.get("reason")
        row.skip_reason = str(reason) if reason else row.skip_reason
        if _as_int(payload.get("checkpoint_turn")) is not None:
            row.checkpoint_turn = _as_int(payload.get("checkpoint_turn"))
        row.waiting_open_since_ms = None

    def _on_waiting_open(self, record: EventRecord) -> None:
        """Note that a root is waiting for its IDE window; a soft remind only."""
        payload = record.payload
        root_id = _root_id(payload, record) or self._index.root_for_thread(
            payload.get("worker_thread")
        )
        if not root_id:
            return
        row = self._root(root_id, record)
        worker = payload.get("worker_thread")
        if worker and row.worker_thread is None:
            row.worker_thread = str(worker)
            self._index.link_root_worker_thread(root_id, str(worker))
        if row.waiting_open_since_ms is None:
            row.waiting_open_since_ms = record.ts_unix_ms
        if row.state in ("unknown", "in_flight"):
            row.state = "waiting_open"

    def _on_error(self, record: EventRecord) -> None:
        """Record the most recent tick error. Zero of these is the normal case."""
        self.last_error_ms = record.ts_unix_ms
        message = (
            record.payload.get("reason")
            or record.payload.get("message")
            or record.payload.get("error")
        )
        self.last_error_message = str(message) if message else "unspecified"

    def _on_intent_healed(self, record: EventRecord) -> None:
        """Clear a healed orphan admit intent from the root's row."""
        root_id = _root_id(record.payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        if row.state == "in_flight":
            row.state = "intent_healed"
        row.admitted_at_ms = None

    def _on_lifecycle(self, record: EventRecord) -> None:
        """Fold tick loop start/stop — v3 §4 ``on_lifecycle``."""
        if record.signal == signals.CHARTER_STARTED:
            self.loop_state = "running"
            # Fresh loop boot — prior process errors are not this loop's fault.
            self.last_error_ms = None
            self.last_error_message = None
        elif record.signal == signals.CHARTER_STOPPED:
            self.loop_state = "stopped"

    def _on_reload(self, record: EventRecord) -> None:
        """Resolve a charter_reload command — v3 §4 ``on_reload``."""
        payload = record.payload
        modules = payload.get("modules")
        if isinstance(modules, (list, tuple)):
            self.reload_module_count = len(modules)
        elif _as_int(payload.get("count")) is not None:
            self.reload_module_count = _as_int(payload.get("count")) or 0
        self.last_reload_ms = record.ts_unix_ms

    def _on_window_failed(self, record: EventRecord) -> None:
        """Mark a root failed pending human re-arm — v3 §4 ``on_window_failed``."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        reason = payload.get("reason")
        row.skip_reason = str(reason) if reason else None
        row.state = "failed"
        row.waiting_open_since_ms = None

    def _on_hold_armed(self, record: EventRecord) -> None:
        """Durable tick hold armed or heartbeat — board reads ``hold_active``."""
        payload = record.payload
        self.hold_active = True
        reason = payload.get("reason")
        if reason:
            self.hold_reason = str(reason)

    def _on_hold_cleared(self, record: EventRecord) -> None:
        """Durable tick hold cleared — next interval runs a normal tick."""
        self.hold_active = False
        self.hold_reason = None

    def _on_root_blocked(self, record: EventRecord) -> None:
        """Operator per-root hold — ledger BLOCKED; stops new admits only."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        row.state = "blocked"
        if payload.get("unenrolled"):
            row.unenrolled = True
        reason = payload.get("reason")
        if reason:
            row.skip_reason = str(reason)

    def _on_root_unblocked(self, record: EventRecord) -> None:
        """Operator cleared per-root hold — BLOCKED returns to IDLE admits."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        if row.closed:
            return
        row.state = "idle"
        if payload.get("reenrolled"):
            row.unenrolled = False

    def _on_consult_queued(self, record: EventRecord) -> None:
        """Mark consult queue posture; streak advances on subsequent scans."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        row.state = "consult_queued"
        row.closed = False
        gid = payload.get("gid")
        if gid:
            row.pickup_gid = str(gid).strip() or row.pickup_gid

    def _on_identical_work_refire_refused(self, record: EventRecord) -> None:
        """Refire refusal on a repeated work_key — wedge visible on the board."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self._root(root_id, record)
        row.refire_refused_streak += 1
        row.state = "stuck"
        friction = payload.get("friction_id")
        work_key = payload.get("work_key")
        detail = f"identical_work_refire work_key={work_key or '?'}"
        if friction is not None:
            detail = f"{detail} friction={friction}"
        row.skip_reason = detail

    def _on_informational_root(self, record: EventRecord) -> None:
        """Root-keyed telemetry (audit/transition/shadow) — no state flip."""
        payload = record.payload
        root_id = _root_id(payload, record)
        if not root_id:
            return
        row = self.roots.get(root_id)
        if row is None:
            return
        if row.last_signal_ms is None or record.ts_unix_ms >= row.last_signal_ms:
            row.last_signal_ms = record.ts_unix_ms
            row.last_signal = record.signal
        transition = payload.get("transition")
        if (
            record.signal == signals.CHARTER_TRANSITION
            and transition == "HEAL_CONSULT_QUEUED"
        ):
            row.state = "idle"
            row.consult_queued_streak = 0
            row.refire_refused_streak = 0
            row.skip_reason = None

    def _on_telemetry_ack(self, _record: EventRecord) -> None:
        """Global telemetry with no root row (e.g. shadow.starved) — swallow only."""
        return

    def _on_audit(self, record: EventRecord) -> None:
        """Seed cold-start state from a windowed audit snapshot.

        The audit reports lists of roots by disposition over a window. It is seed
        material: it establishes that a root exists and its coarse posture, and is
        superseded by any later live transition. It never closes a root, because
        an audit listing is a summary and ``root_closed`` is the authority.
        """
        payload = record.payload
        self.cold_start_seeded = True
        for key, state in (
            ("admitted", "in_flight"),
            ("closed", "window_closed"),
            ("failed", "failed"),
            ("waiting_open", "waiting_open"),
        ):
            entries = payload.get(key)
            if not isinstance(entries, (list, tuple)):
                continue
            for entry in entries:
                root_id, worker = _audit_entry(entry)
                if not root_id:
                    continue
                row = self.roots.get(root_id)
                if row is None:
                    row = RootState(root_id)
                    self.roots[root_id] = row
                    row.state = state
                    row.last_signal_ms = record.ts_unix_ms
                    row.last_signal = record.signal
                if worker:
                    row.worker_thread = worker
                    self._index.link_root_worker_thread(root_id, worker)


def _audit_entry(entry: Any) -> tuple[str | None, str | None]:
    """Normalise one audit list entry to ``(root_id, worker_thread)``."""
    if isinstance(entry, Mapping):
        root = entry.get("root") or entry.get("root_id")
        worker = entry.get("worker_thread")
        return (str(root) if root else None, str(worker) if worker else None)
    if isinstance(entry, (str, int)):
        return (str(entry), None)
    return (None, None)
