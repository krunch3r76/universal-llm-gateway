"""SdkFold -- folds cursor-sdk dispatches, reconciling the GS2 dual emitters.

GS2 in one sentence: **one dispatch, two emitters.** The same cursor-sdk terminal
is observable from the worker lane (``frontier.sdk.worker.completed``) and from
the pipeline lane (``pipeline.frontier.dispatch.completed``); both are attested
as live signals in Cortex, both carry ``execution_id``, and both claim to describe
the dispatch's outcome. A fold that treats them as independent double-counts; a
fold that lets the later one overwrite the earlier silently discards evidence.

Reconstruction note: the v3 spec that defines the GS2 label is unreadable from a
CDP seat, so the *shape* of GS2 above is reconstructed from two attested sources
-- Fable §3.4 ("``source``... required to disambiguate the GS2 dual-emitter case")
and friction 22940, where dual-emitter divergence between a stream-fold path and a
manifest path was a real shipped defect whose fix kept the second emitter as a
cross-check rather than deleting it. G5 confirms against v3.

Bind, three rules:

1. **Idempotent** -- keyed on ``execution_id``, so a reconnect + ``resume_from``
   overlap replaying the same terminal changes nothing.
2. **First terminal wins** the timing fields. A later terminal from the other lane
   never rewrites ``terminal_ms`` or ``state``.
3. **Divergence is reported, never resolved.** When the second lane's terminal
   disagrees on a material field, the field name lands in ``divergent_fields`` and
   raises an attention item. Picking a winner would hide the defect this monitor
   exists to surface.

Further negative space: a judgment-gap dispatch -- one admitted but which never
emitted a start -- produces **no SDK row**. Rows come from observed dispatch
events only; the core does not synthesise a row from a charter admission, because
"admitted" and "the worker actually started" are different claims.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import signals
from ..correlation import CorrelationIndex
from ..protocols import EventRecord, envelope_source, envelope_subject

#: Terminal fields compared across emitters. Chosen because a disagreement on any
#: of them changes what an operator would *do*, unlike e.g. a log line.
MATERIAL_FIELDS = ("state", "prompt_tokens", "completion_tokens", "failure_reason")


class SdkState:
    """Mutable per-dispatch accumulator."""

    __slots__ = (
        "dispatch_id",
        "state",
        "root_id",
        "thread_id",
        "seat",
        "role",
        "model",
        "contract",
        "started_ms",
        "last_progress_ms",
        "terminal_ms",
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
        "stall_stage",
        "failure_reason",
        "emitters_seen",
        "divergent_fields",
        "terminal_emitter",
        "provenance",
        "queue_position",
        "source_repo",
        "delivery_failed",
        "closeout_uri",
        "pre_park_state",
    )

    def __init__(self, dispatch_id: str) -> None:
        self.dispatch_id = dispatch_id
        self.state = "unknown"
        self.root_id: str | None = None
        self.thread_id: str | None = None
        self.seat: str | None = None
        self.role: str | None = None
        self.model: str | None = None
        self.contract: str | None = None
        self.started_ms: int | None = None
        self.last_progress_ms: int | None = None
        self.terminal_ms: int | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.cached_tokens: int | None = None
        self.stall_stage: str | None = None
        self.failure_reason: str | None = None
        self.emitters_seen: list[str] = []
        self.divergent_fields: list[str] = []
        self.terminal_emitter: str | None = None
        self.provenance: str | None = None
        self.queue_position: int | None = None
        self.source_repo: str | None = None
        self.delivery_failed = False
        self.closeout_uri: str | None = None
        self.pre_park_state: str | None = None


def _as_int(value: Any) -> int | None:
    """Coerce ``value`` to ``int``, returning ``None`` when it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dispatch_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve the dispatch key, preferring ``execution_id`` as both lanes carry it."""
    for key in ("execution_id", "dispatch_id", "worker_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return envelope_subject(record)


def _queued_dispatch_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve dispatch key for ``worker.queued`` — GS2 branch on payload shape."""
    origin = payload.get("origin_service")
    if origin == "stargate" or (
        payload.get("request_id") is not None and payload.get("source_repo") is None
    ):
        for key in ("execution_id", "dispatch_id", "request_id"):
            value = payload.get(key)
            if value:
                return str(value)
        return None
    return _dispatch_id(payload, record)


def _lease_row_id(payload: Mapping[str, Any], key: str) -> str | None:
    """Resolve a lease/park row id from ``parent_id`` / ``child_id`` / ``dispatch_id``."""
    value = payload.get(key)
    if value:
        return str(value)
    return None


class SdkFold:
    """Accumulates one row per cursor-sdk dispatch across both emitter lanes."""

    def __init__(self, index: CorrelationIndex) -> None:
        self._index = index
        self.dispatches: dict[str, SdkState] = {}

    def handlers(self) -> dict[str, Any]:
        """Return this fold's signal-to-handler table."""
        table: dict[str, Any] = {}
        for signal in (
            signals.MONITOR_META_SDK_STARTED,
            signals.SDK_PIPELINE_STARTED,
        ):
            table[signal] = self._on_started
        table[signals.SDK_WORKER_PROGRESS] = self._on_progress
        for signal in sorted(signals.SDK_TERMINAL_SIGNALS):
            table[signal] = self._on_terminal
        table[signals.SDK_WORKER_QUEUED] = self._on_queued
        table[signals.SDK_WORKER_TIMEOUT] = self._on_timeout
        table[signals.SDK_WORKER_ORPHANED] = self._on_orphaned
        table[signals.SDK_WORKER_DELIVERY_FAILED] = self._on_delivery_failed
        table[signals.SDK_LEASE_PROMOTED] = self._on_lease_promoted
        table[signals.SDK_LEASE_RELEASED] = self._on_lease_released
        table[signals.SDK_LEASE_PARK_ENTER] = self._on_park_enter
        table[signals.SDK_LEASE_PARK_RESTORE] = self._on_park_restore
        table[signals.SDK_CLOSEOUT_RELOCATED] = self._on_closeout_relocated
        return table

    def _row_for_id(self, dispatch_id: str, record: EventRecord) -> SdkState:
        """Return (creating if needed) the accumulator for an explicit dispatch id."""
        row = self.dispatches.get(dispatch_id)
        if row is None:
            row = SdkState(dispatch_id)
            self.dispatches[dispatch_id] = row
        self._absorb_identity(row, record)
        self._apply_provenance(row, record)
        return row

    def _state(self, record: EventRecord) -> SdkState | None:
        """Return (creating if needed) the accumulator for this record's dispatch."""
        dispatch_id = _dispatch_id(record.payload, record)
        if not dispatch_id:
            return None
        row = self.dispatches.get(dispatch_id)
        if row is None:
            row = SdkState(dispatch_id)
            self.dispatches[dispatch_id] = row
        emitter = signals.SDK_EMITTER_BY_SIGNAL.get(
            record.signal, envelope_source(record) or "unknown"
        )
        if emitter not in row.emitters_seen:
            row.emitters_seen.append(emitter)
        self._absorb_identity(row, record)
        self._apply_provenance(row, record)
        return row

    def _apply_provenance(self, row: SdkState, record: EventRecord) -> None:
        """Track row provenance; live signals upgrade, reconciled never clobbers."""
        payload = record.payload
        if payload.get(signals.PROVENANCE_RECONCILED_KEY) == signals.PROVENANCE_RECONCILED:
            if row.provenance != "signal":
                row.provenance = "reconciled"
            return
        row.provenance = "signal"

    def _absorb_identity(self, row: SdkState, record: EventRecord) -> None:
        """Fill identity fields from whichever emitter happened to carry them.

        Identity is additive and never cleared: the two lanes populate different
        subsets, so first-writer-wins on each field yields the union without
        letting a sparse later event blank a known value.
        """
        payload = record.payload
        for src, dst in (
            ("seat", "seat"),
            ("role", "role"),
            ("model", "model"),
            ("contract", "contract"),
            ("thread_id", "thread_id"),
            ("dispatch_thread_id", "thread_id"),
            ("worker_thread", "thread_id"),
            ("root", "root_id"),
            ("root_id", "root_id"),
            ("source_ref", "root_id"),
        ):
            if getattr(row, dst) is None and payload.get(src):
                setattr(row, dst, str(payload[src]))
        self._index.link_dispatch(row.dispatch_id, row.root_id, row.thread_id)
        if row.root_id is None:
            row.root_id = self._index.root_for_dispatch(row.dispatch_id)

    # --- handlers ---------------------------------------------------------
    def _advance_progress(self, row: SdkState, ts_unix_ms: int) -> None:
        """Move the idle clock forward only.

        Monotone on purpose. A duplicate re-delivered across a ``resume_from``
        overlap arrives with an *older* timestamp than the state it is replaying, so
        a plain assignment would rewind the idle clock and manufacture staleness on
        a healthy dispatch. Terminal rows are frozen outright.
        """
        if row.terminal_ms is not None:
            return
        if row.last_progress_ms is None or ts_unix_ms > row.last_progress_ms:
            row.last_progress_ms = ts_unix_ms

    def _on_started(self, record: EventRecord) -> None:
        """Open or refresh a dispatch row on a start from either lane."""
        row = self._state(record)
        if row is None:
            return
        if row.started_ms is None or record.ts_unix_ms < row.started_ms:
            row.started_ms = record.ts_unix_ms
        self._advance_progress(row, record.ts_unix_ms)
        if row.terminal_ms is None:
            row.state = "running"

    def _on_progress(self, record: EventRecord) -> None:
        """Advance the idle clock. Progress is the only thing that resets it."""
        row = self._state(record)
        if row is None:
            return
        self._advance_progress(row, record.ts_unix_ms)
        if row.terminal_ms is None:
            row.state = "running"
        stage = record.payload.get("stall_stage") or record.payload.get("phase")
        if stage and row.terminal_ms is None:
            row.stall_stage = str(stage)

    def _on_terminal(self, record: EventRecord) -> None:
        """Fold a terminal from either lane; reconcile rather than overwrite."""
        row = self._state(record)
        if row is None:
            return
        payload = record.payload
        emitter = signals.SDK_EMITTER_BY_SIGNAL.get(record.signal, "unknown")
        failed = record.signal in signals.SDK_FAILURE_SIGNALS
        status = payload.get("status")
        observed = {
            "state": "failed" if failed else str(status or "completed"),
            "prompt_tokens": _as_int(payload.get("prompt_tokens")),
            "completion_tokens": _as_int(payload.get("completion_tokens")),
            "failure_reason": _first_str(
                payload, ("failure_reason", "error", "stall_stage")
            ),
        }
        if row.terminal_ms is None:
            self._accept_first_terminal(row, record, emitter, observed, payload)
            return
        for name in MATERIAL_FIELDS:
            claimed = observed.get(name)
            if claimed is None:
                continue
            if getattr(row, name) != claimed and name not in row.divergent_fields:
                row.divergent_fields.append(name)

    def _accept_first_terminal(
        self,
        row: SdkState,
        record: EventRecord,
        emitter: str,
        observed: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        """Bind the first terminal seen for a dispatch. Later ones only compare."""
        self._advance_progress(row, record.ts_unix_ms)
        row.terminal_ms = record.ts_unix_ms
        row.terminal_emitter = emitter
        row.state = str(observed["state"])
        for name in ("prompt_tokens", "completion_tokens"):
            if observed.get(name) is not None:
                setattr(row, name, observed[name])
        if _as_int(payload.get("cached_tokens")) is not None:
            row.cached_tokens = _as_int(payload.get("cached_tokens"))
        if observed.get("failure_reason"):
            row.failure_reason = str(observed["failure_reason"])
        if payload.get("stall_stage"):
            row.stall_stage = str(payload["stall_stage"])

    def _on_queued(self, record: EventRecord) -> None:
        """Fold FIFO queue placement — GS2 branch on stargate vs git_worker shape."""
        payload = record.payload
        dispatch_id = _queued_dispatch_id(payload, record)
        if not dispatch_id:
            return
        row = self._row_for_id(dispatch_id, record)
        if row.terminal_ms is not None:
            return
        row.state = "queued"
        if _as_int(payload.get("queue_position")) is not None:
            row.queue_position = _as_int(payload.get("queue_position"))
        if payload.get("source_repo"):
            row.source_repo = str(payload["source_repo"])
        self._advance_progress(row, record.ts_unix_ms)

    def _on_timeout(self, record: EventRecord) -> None:
        """Terminal: worker exceeded wall budget (v3 §5)."""
        self._bind_lifecycle_terminal(record, state="timeout")

    def _on_orphaned(self, record: EventRecord) -> None:
        """Terminal: bridge lost while worker still running (v3 §5)."""
        payload = record.payload
        reason = payload.get("terminal_status") or payload.get("bridge_aborted")
        self._bind_lifecycle_terminal(
            record, state="orphaned", failure_reason=str(reason) if reason else None
        )

    def _on_delivery_failed(self, record: EventRecord) -> None:
        """Run succeeded but on-behalf bus post failed — non-terminal (v3 §5/§9)."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        payload = record.payload
        row.delivery_failed = True
        if row.state in ("unknown", "running", "queued"):
            row.state = "completed"
        code = payload.get("status_code")
        sidecar = payload.get("sidecar_ref")
        detail = f"status_code={code}" if code is not None else "bus delivery failed"
        if sidecar:
            detail = f"{detail}; sidecar={sidecar}"
        row.failure_reason = detail
        self._advance_progress(row, record.ts_unix_ms)

    def _on_lease_promoted(self, record: EventRecord) -> None:
        """FIFO advance — queued dispatch becomes lease holder (v3 §5)."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        row.state = "running"
        row.queue_position = None
        self._advance_progress(row, record.ts_unix_ms)

    def _on_lease_released(self, record: EventRecord) -> None:
        """Write lease released for a dispatch (v3 §5)."""
        row = self._state(record)
        if row is None:
            return
        if payload := record.payload:
            if payload.get("source_repo") and row.source_repo is None:
                row.source_repo = str(payload["source_repo"])

    def _on_park_enter(self, record: EventRecord) -> None:
        """Parent yields lease to nested child — parent → ``parked_waiting`` (v3 §5)."""
        payload = record.payload
        parent_id = _lease_row_id(payload, "parent_id")
        child_id = _lease_row_id(payload, "child_id")
        if parent_id:
            parent = self._row_for_id(parent_id, record)
            if parent.terminal_ms is None and parent.state != "parked_waiting":
                parent.pre_park_state = parent.state
                parent.state = "parked_waiting"
        if child_id:
            child = self._row_for_id(child_id, record)
            if child.terminal_ms is None:
                child.state = "running"
                self._advance_progress(child, record.ts_unix_ms)

    def _on_park_restore(self, record: EventRecord) -> None:
        """Child terminal returns lease to parent — restore prior parent state (v3 §5)."""
        payload = record.payload
        parent_id = _lease_row_id(payload, "parent_id")
        if not parent_id:
            return
        parent = self.dispatches.get(parent_id)
        if parent is None or parent.terminal_ms is not None:
            return
        restored = parent.pre_park_state or "running"
        parent.state = restored
        parent.pre_park_state = None
        parent.last_progress_ms = record.ts_unix_ms

    def _on_closeout_relocated(self, record: EventRecord) -> None:
        """Record durable closeout URI when inline body exceeds limits (v3 §5)."""
        row = self._state(record)
        if row is None:
            return
        uri = record.payload.get("uri")
        if uri:
            row.closeout_uri = str(uri)

    def _bind_lifecycle_terminal(
        self,
        record: EventRecord,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        """Bind a worker-lane lifecycle terminal (timeout/orphaned)."""
        row = self._state(record)
        if row is None:
            return
        payload = record.payload
        if row.terminal_ms is not None:
            return
        self._advance_progress(row, record.ts_unix_ms)
        row.terminal_ms = record.ts_unix_ms
        row.terminal_emitter = "worker"
        row.state = state
        reason = failure_reason or _first_str(payload, ("error", "failure_reason"))
        if reason:
            row.failure_reason = reason
        model = payload.get("resolved_model")
        if model and row.model is None:
            row.model = str(model)


def _first_str(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first truthy value among ``keys`` as a string, else ``None``."""
    for key in keys:
        if payload.get(key):
            return str(payload[key])
    return None
