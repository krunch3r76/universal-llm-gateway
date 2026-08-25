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

from collections.abc import Mapping
from typing import Any

from .. import signals
from ..correlation import CorrelationIndex
from ..protocols import EventRecord, envelope_source
from .sdk_handlers import sdk_handler_table
from .sdk_lane import apply_pending_lane
from .sdk_provenance import note_lease_park
from .sdk_review_child import close_terminal_row
from .sdk_state import (
    SdkIdAliases,
    SdkState,
    absorb_tool_call_count,
    ensure_canonical_row,
    note_tool_call_id,
)
from .sdk_state import (
    as_int as _as_int,
)
from .sdk_state import (
    first_str as _first_str,
)
from .sdk_state import (
    lease_row_id as _lease_row_id,
)
from .sdk_state import (
    payload_alt_ids as _payload_alt_ids,
)

#: Terminal fields compared across emitters. Chosen because a disagreement on any
#: of them changes what an operator would *do*, unlike e.g. a log line.
MATERIAL_FIELDS = ("state", "prompt_tokens", "completion_tokens", "failure_reason")


class SdkFold:
    """Accumulates one row per cursor-sdk dispatch across both emitter lanes."""

    def __init__(self, index: CorrelationIndex) -> None:
        self._index = index
        self.dispatches: dict[str, SdkState] = {}
        self._aliases = SdkIdAliases()
        #: Model stamps from ``generate.requested`` before a dispatch row exists.
        self._pending_models: dict[str, str] = {}
        #: Checkout lane/branch stashed until ``worker.dispatched`` opens the row.
        self._pending_lane: dict[str, str] = {}
        self._pending_branch: dict[str, str] = {}
        #: Refused admits — attention only, never a live row.
        self.duplicate_refused: dict[str, tuple[int, str, str]] = {}
        #: Evidence-only park edges ``(parent_id, child_id)`` in first-seen order.
        self.lease_parks: list[tuple[str, str]] = []

    def handlers(self) -> dict[str, Any]:
        """Return this fold's signal-to-handler table."""
        return sdk_handler_table(self)

    def _resolve_row(
        self,
        record: EventRecord,
        preferred_id: str | None,
        *,
        payload_ids: tuple[str, ...] | None = None,
        queued: bool = False,
    ) -> SdkState | None:
        """Resolve canonical row, collapsing live alt-id siblings when needed."""
        if not preferred_id:
            return None
        ids = payload_ids or _payload_alt_ids(
            record.payload, record, queued=queued
        )[1]
        row = ensure_canonical_row(
            self.dispatches, self._aliases, preferred_id, ids
        )
        emitter = signals.SDK_EMITTER_BY_SIGNAL.get(
            record.signal, envelope_source(record) or "unknown"
        )
        if emitter not in row.emitters_seen:
            row.emitters_seen.append(emitter)
        self._absorb_identity(row, record)
        self._apply_provenance(row, record)
        self._apply_pending_model(row)
        apply_pending_lane(self, row)
        return row

    def _row_for_id(self, dispatch_id: str, record: EventRecord) -> SdkState:
        """Return (creating if needed) the accumulator for an explicit dispatch id."""
        _, payload_ids = _payload_alt_ids(record.payload, record)
        merged_ids = tuple(dict.fromkeys((dispatch_id, *payload_ids)))
        row = self._resolve_row(record, dispatch_id, payload_ids=merged_ids)
        assert row is not None
        return row

    def _state(self, record: EventRecord) -> SdkState | None:
        """Return (creating if needed) the accumulator for this record's dispatch."""
        preferred, payload_ids = _payload_alt_ids(record.payload, record)
        return self._resolve_row(record, preferred, payload_ids=payload_ids)

    def _apply_pending_model(self, row: SdkState) -> None:
        """Apply a stashed generate.requested model once the dispatch row exists."""
        if row.model is not None or not self._pending_models:
            return
        for key, model in self._pending_models.items():
            if row.dispatch_id == key or row.dispatch_id.startswith(f"{key}-"):
                row.model = model
                return

    def _on_generate_requested(self, record: EventRecord) -> None:
        """Stamp model early — often the only model source while queued."""
        payload = record.payload
        model = payload.get("resolved_model") or payload.get("model")
        if not model:
            return
        model_s = str(model)
        request_id = payload.get("request_id")
        execution_id = payload.get("execution_id")
        if request_id:
            self._pending_models[str(request_id)] = model_s
        if execution_id:
            self._pending_models[str(execution_id)] = model_s
        for row in self.dispatches.values():
            self._apply_pending_model(row)

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
            ("resolved_model", "model"),
            ("contract", "contract"),
            ("handoff_contract", "contract"),
            ("thread_id", "thread_id"),
            ("dispatch_thread_id", "thread_id"),
            ("worker_thread", "thread_id"),
            ("root", "root_id"),
            ("root_id", "root_id"),
            ("source_ref", "root_id"),
            ("admitted_via", "admitted_via"),
            ("asked_by", "asked_by"),
            ("purpose", "purpose"),
            ("story_id", "story_id"),
            ("topic", "topic"),
            ("nest_under", "nest_under"),
            ("resume_of", "resume_of"),
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
        absorb_tool_call_count(row, record.payload)
        stage = record.payload.get("stall_stage") or record.payload.get("phase")
        if stage and row.terminal_ms is None:
            row.stall_stage = str(stage)

    def _on_toolcall(self, record: EventRecord) -> None:
        """Last-tool overlay + live tc bump; also advances idle clock."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        payload = record.payload
        name = payload.get("tool_name")
        status = payload.get("status")
        if name:
            row.last_tool_name = str(name)
        if status:
            row.last_tool_status = str(status)
        call_id = payload.get("call_id")
        note_tool_call_id(row, str(call_id) if call_id else None)
        self._advance_progress(row, record.ts_unix_ms)
        if row.state in ("unknown", "queued"):
            row.state = "running"

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
        absorb_tool_call_count(row, payload)
        close_terminal_row(
            self,
            row,
            record,
            state=str(observed["state"]),
            failure_reason=row.failure_reason,
            emitter=emitter,
        )

    def _on_queued(self, record: EventRecord) -> None:
        """Fold FIFO queue placement — GS2 branch on stargate vs git_worker shape."""
        payload = record.payload
        preferred, payload_ids = _payload_alt_ids(payload, record, queued=True)
        if not preferred:
            return
        row = self._resolve_row(
            record, preferred, payload_ids=payload_ids, queued=True
        )
        if row is None:
            return
        if row.started_ms is not None:
            return
        if row.terminal_ms is not None:
            return
        row.state = "queued"
        if _as_int(payload.get("queue_position")) is not None:
            row.queue_position = _as_int(payload.get("queue_position"))
        if payload.get("source_repo"):
            row.source_repo = str(payload["source_repo"])
        if row.model is None:
            for key in ("resolved_model", "model", "model_id"):
                if payload.get(key):
                    row.model = str(payload[key])
                    break
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

    def _on_cancelled(self, record: EventRecord) -> None:
        """Terminal: supersede / operator cancel interrupted the worker."""
        payload = record.payload
        method = payload.get("method")
        reason = payload.get("reason") or payload.get("error")
        detail_parts = [p for p in (method, reason) if p]
        detail = ": ".join(str(p) for p in detail_parts) if detail_parts else None
        self._bind_lifecycle_terminal(
            record, state="cancelled", failure_reason=detail
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

    def _on_implement_source_ref_unresolved(self, record: EventRecord) -> None:
        """Flag implement admit without resolvable source_ref (readiness gate bypass)."""
        payload = record.payload
        dispatch_id = payload.get("dispatch_id")
        if not dispatch_id:
            return
        row = self._row_for_id(str(dispatch_id), record)
        row.implement_gate_bypass = True
        if payload.get("thread_id") and row.thread_id is None:
            row.thread_id = str(payload["thread_id"])
        if row.contract is None:
            row.contract = "implement"
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
        """Write lease released for a dispatch (v3 §5).

        When still LIVE, sets ``lease_released_without_terminal`` for G4 attention.
        Clearing LIVE requires a worker terminal — applied live or via ulg backfill
        (``terminal_backfill``); this handler never invents ``terminal_ms``.
        """
        row = self._state(record)
        if row is None:
            return
        if payload := record.payload:
            if payload.get("source_repo") and row.source_repo is None:
                row.source_repo = str(payload["source_repo"])
        if (
            row.terminal_ms is None
            and row.state != "parked_waiting"
        ):
            row.lease_released_without_terminal = True

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
        if parent_id and child_id:
            note_lease_park(self, parent_id, child_id)

    def _on_park_restore(self, record: EventRecord) -> None:
        """Child terminal returns lease to parent — restore prior parent state (v3 §5)."""
        payload = record.payload
        parent_id = _lease_row_id(payload, "parent_id")
        if not parent_id:
            return
        parent_id = self._aliases.resolve(parent_id)
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

    def _on_closeout_reconciled(self, record: EventRecord) -> None:
        """Filesystem ground truth suppressed a would-be closeout degrade (v3 §5)."""
        row = self._state(record)
        if row is None:
            return
        path = record.payload.get("verifying_path")
        if path and row.closeout_uri is None:
            row.closeout_uri = str(path)

    def _on_closeout_relayed(self, record: EventRecord) -> None:
        """Record relayed closeout receipt — identity + URI, never terminalize (v3 §5)."""
        row = self._state(record)
        if row is None:
            return
        payload = record.payload
        if row.closeout_uri is None:
            uri = payload.get("receipt_path") or payload.get("uri")
            if uri:
                row.closeout_uri = str(uri)

    def _bind_lifecycle_terminal(
        self,
        record: EventRecord,
        *,
        state: str,
        failure_reason: str | None = None,
    ) -> None:
        """Bind a worker-lane lifecycle terminal (timeout/orphaned/cancelled)."""
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
        close_terminal_row(
            self,
            row,
            record,
            state=state,
            failure_reason=row.failure_reason,
            emitter="worker",
        )
