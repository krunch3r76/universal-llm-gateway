"""CdpFold -- folds the ``cdp.generate.*`` family into CDP leg rows.

Family status: **prospective.** The CDP lane emits no events today
(``cortex://notes/system/threads/5718-session-review-substrate-apis.md``: "the
entire CDP lane emits zero events"). The G4 scope packet assigns this family to
this fold, so the fold is the *consumer half of a contract the emitter side has
yet to honour*. Until it does, ``cdp`` derives empty -- which is correct, not
broken. Payload keys below are taken from the satellite's attested vocabulary
(``execution_id``, ``status``, ``archive_uri``, ``content_proof_uri``,
``stall_stage``, ``picker_model``, ``dispatch_thread_id``) rather than invented.

Negative space, and the reason this fold is deliberately dull:

* **Silence is not failure.** A leg that stops emitting keeps its last observed
  state. Absence of progress produces an *idle attention item*, never a folded
  ``failed``. A CDP browser execution can be genuinely alive and quiet.
* **Idle, never wall-clock.** The staleness signal is time since last progress
  (``[universal:obs-over-timeouts]``). A leg that keeps reporting progress is
  never stale however long it runs. ``max_wall_s`` on the caller side is a cost
  ceiling, not a completion judgment, and the fold does not model it.
* **``completed`` without proof is not success.** A terminal carrying neither
  ``archive_uri`` nor ``content_proof_uri`` folds to state ``completed`` with
  ``proof_present=False`` and earns its own attention kind. Cortex records this as
  a live gap: the adapter treats a proofless ``completed`` as unremarkable and
  reports a generic stall up to 600s later.
* **The satellite's status vocabulary is the authority** -- ``pending`` /
  ``running`` / ``completed`` / ``failed`` / ``aborted``. Cortex documents the
  adapter drifting to ``cancelled`` (unreachable) and ``queued`` (should be
  ``pending``); this fold maps unknown status strings to ``unknown`` and counts
  them rather than guessing an alias.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import signals
from ..correlation import CorrelationIndex
from ..protocols import EventRecord, envelope_subject

#: The satellite's ``ExecutionStatus`` literal set, verbatim.
SATELLITE_STATUSES = ("pending", "running", "completed", "failed", "aborted")


class CdpState:
    """Mutable per-leg accumulator."""

    __slots__ = (
        "execution_id",
        "state",
        "picker_model",
        "dispatch_thread_id",
        "root_id",
        "prompt_uri",
        "submitted_ms",
        "last_progress_ms",
        "terminal_ms",
        "archive_uri",
        "content_proof_uri",
        "stall_stage",
        "failure_reason",
    )

    def __init__(self, execution_id: str) -> None:
        self.execution_id = execution_id
        self.state = "unknown"
        self.picker_model: str | None = None
        self.dispatch_thread_id: str | None = None
        self.root_id: str | None = None
        self.prompt_uri: str | None = None
        self.submitted_ms: int | None = None
        self.last_progress_ms: int | None = None
        self.terminal_ms: int | None = None
        self.archive_uri: str | None = None
        self.content_proof_uri: str | None = None
        self.stall_stage: str | None = None
        self.failure_reason: str | None = None


def _execution_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve the leg key from the payload or the envelope subject."""
    for key in ("execution_id", "leg_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return envelope_subject(record)


class CdpFold:
    """Accumulates one row per CDP generate leg."""

    def __init__(self, index: CorrelationIndex) -> None:
        self._index = index
        self.legs: dict[str, CdpState] = {}
        self.unknown_statuses: dict[str, int] = {}

    def handlers(self) -> dict[str, Any]:
        """Return this fold's signal-to-handler table."""
        return {
            signals.CDP_SUBMITTED: self._on_submitted,
            signals.CDP_RUNNING: self._on_progress,
            signals.CDP_PROGRESS: self._on_progress,
            signals.CDP_STALLED: self._on_stalled,
            signals.CDP_COMPLETED: self._on_terminal,
            signals.CDP_FAILED: self._on_terminal,
            signals.CDP_ABORTED: self._on_terminal,
        }

    def _state(self, record: EventRecord) -> CdpState | None:
        """Return (creating if needed) the accumulator for this record's leg."""
        execution_id = _execution_id(record.payload, record)
        if not execution_id:
            return None
        row = self.legs.get(execution_id)
        if row is None:
            row = CdpState(execution_id)
            self.legs[execution_id] = row
        payload = record.payload
        for src, dst in (
            ("picker_model", "picker_model"),
            ("model", "picker_model"),
            ("dispatch_thread_id", "dispatch_thread_id"),
            ("prompt_uri", "prompt_uri"),
            ("root", "root_id"),
            ("root_id", "root_id"),
        ):
            if getattr(row, dst) is None and payload.get(src):
                setattr(row, dst, str(payload[src]))
        if row.root_id:
            self._index.link_cdp_leg(execution_id, row.root_id)
        else:
            row.root_id = self._index.root_for_thread(row.dispatch_thread_id)
        return row

    def _advance_progress(self, row: CdpState, ts_unix_ms: int) -> None:
        """Move the idle clock forward only; terminal rows are frozen.

        Same monotone rule as the SDK fold, for the same reason: a duplicate
        re-delivered across a ``resume_from`` overlap carries an older timestamp,
        and assigning it would rewind the idle clock and invent staleness.
        """
        if row.terminal_ms is not None:
            return
        if row.last_progress_ms is None or ts_unix_ms > row.last_progress_ms:
            row.last_progress_ms = ts_unix_ms

    def _record_status(self, row: CdpState, payload: Mapping[str, Any]) -> None:
        """Adopt an explicit ``status`` only when it is in the satellite's set."""
        status = payload.get("status")
        if not status:
            return
        status = str(status)
        if status in SATELLITE_STATUSES:
            row.state = status
        else:
            self.unknown_statuses[status] = self.unknown_statuses.get(status, 0) + 1

    # --- handlers ---------------------------------------------------------
    def _on_submitted(self, record: EventRecord) -> None:
        """Open a leg row on submit."""
        row = self._state(record)
        if row is None:
            return
        if row.submitted_ms is None or record.ts_unix_ms < row.submitted_ms:
            row.submitted_ms = record.ts_unix_ms
        self._advance_progress(row, record.ts_unix_ms)
        if row.terminal_ms is None:
            row.state = "pending"
            self._record_status(row, record.payload)

    def _on_progress(self, record: EventRecord) -> None:
        """Advance the idle clock and, if still live, mark the leg running."""
        row = self._state(record)
        if row is None:
            return
        self._advance_progress(row, record.ts_unix_ms)
        if row.terminal_ms is None:
            row.state = "running"
            self._record_status(row, record.payload)

    def _on_stalled(self, record: EventRecord) -> None:
        """Record an emitter-declared stall stage without declaring the leg dead.

        ``stalled`` is a diagnosis from the caller's poll ladder, not a terminal.
        The leg keeps its state; only an explicit terminal ends it.
        """
        row = self._state(record)
        if row is None:
            return
        stage = record.payload.get("stall_stage")
        row.stall_stage = str(stage) if stage else "unspecified"

    def _on_terminal(self, record: EventRecord) -> None:
        """Fold an explicit terminal. Idempotent: the first terminal wins."""
        row = self._state(record)
        if row is None:
            return
        payload = record.payload
        if row.terminal_ms is not None:
            return
        self._advance_progress(row, record.ts_unix_ms)
        row.terminal_ms = record.ts_unix_ms
        row.state = {
            signals.CDP_COMPLETED: "completed",
            signals.CDP_FAILED: "failed",
            signals.CDP_ABORTED: "aborted",
        }.get(record.signal, "unknown")
        self._record_status(row, payload)
        for src, dst in (
            ("archive_uri", "archive_uri"),
            ("content_proof_uri", "content_proof_uri"),
        ):
            if payload.get(src):
                setattr(row, dst, str(payload[src]))
        for key in ("failure_reason", "error", "stall_stage"):
            if payload.get(key):
                row.failure_reason = str(payload[key])
                break
        if payload.get("stall_stage"):
            row.stall_stage = str(payload["stall_stage"])
