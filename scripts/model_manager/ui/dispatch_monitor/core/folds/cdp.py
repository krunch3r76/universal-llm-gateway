"""CdpFold -- folds live ``cdp.generate.*`` and CDP ``frontier.poll.hint.issued`` rows.

Authority: v3 §6 handler table (G5.2 slice 1). ``request_id`` is the sole leg key
present on every ``cdp.generate.*`` payload. The G3 leg is a black box between
``admitted`` and terminal — no mid-flight **lifecycle** progress exists on the
wire (§6.2). Observation signals in ``CDP_OBSERVATION_SIGNALS`` are declared and
ignored at the Model gate; they must not set or hold ``terminal_ms``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .. import signals
from ..correlation import CorrelationIndex
from ..protocols import EventRecord
from .cdp_chat import apply_pending_chat, normalize_chat_url, stash_or_stamp_chat

#: Default wall from ``libs/claude_bundles/cdp_model_endpoint.py`` — cost ceiling, not completion.
DEFAULT_MAX_WALL_S = 1800

# Keep in sync with libs/claude_bundles/cdp_model_endpoint.CDP_REPLY_FROM
CDP_REPLY_FROM = "web-anthropic"


class CdpState:
    """Mutable per-leg accumulator keyed by ``request_id``."""

    __slots__ = (
        "request_id",
        "execution_id",
        "satellite_execution_id",
        "thread_id",
        "model",
        "caller_agent",
        "topic",
        "chat_url",
        "state",
        "hint_issued_ms",
        "admitted_at_ms",
        "terminal_ms",
        "max_wall_s",
        "archive_uri",
        "content_proof_uri",
        "stall_stage",
        "failure_reason",
        "root_id",
    )

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.execution_id: str | None = None
        self.satellite_execution_id: str | None = None
        self.thread_id: str | None = None
        self.model: str | None = None
        self.caller_agent: str | None = None
        self.topic: str | None = None
        self.chat_url: str | None = None
        self.state = "unknown"
        self.hint_issued_ms: int | None = None
        self.admitted_at_ms: int | None = None
        self.terminal_ms: int | None = None
        self.max_wall_s = DEFAULT_MAX_WALL_S
        self.archive_uri: str | None = None
        self.content_proof_uri: str | None = None
        self.stall_stage: str | None = None
        self.failure_reason: str | None = None
        self.root_id: str | None = None


def _request_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve the leg key — ``request_id`` is the only key on all five CDP rows."""
    value = payload.get("request_id")
    if value:
        return str(value)
    return None


class CdpFold:
    """Accumulates one row per CDP generate leg, keyed on ``request_id``."""

    def __init__(self, index: CorrelationIndex) -> None:
        self._index = index
        self.legs: dict[str, CdpState] = {}
        self._pending_chat: dict[str, str] = {}

    def handlers(self) -> dict[str, Any]:
        """Return this fold's signal-to-handler table (v3 §6 verbatim)."""
        return {
            signals.POLL_HINT_ISSUED: self._on_poll_hint,
            signals.CDP_ADMITTED: self._on_admitted,
            signals.CDP_SUBMITTED: self._on_submitted,
            signals.CDP_PROOF: self._on_proof,
            signals.CDP_STALLED: self._on_stalled,
            signals.CDP_DELIVERY_FAILED: self._on_delivery_failed,
            signals.AGENTBUS_THREAD_CSE_BOUND: self._on_chat_bind,
            signals.CDP_PROVENANCE_BOUND: self._on_chat_bind,
        }

    def _state(
        self, record: EventRecord, request_id: str | None = None
    ) -> CdpState | None:
        """Return (creating if needed) the accumulator for this record's leg."""
        rid = request_id or _request_id(record.payload, record)
        if not rid:
            return None
        row = self.legs.get(rid)
        if row is None:
            row = CdpState(rid)
            self.legs[rid] = row
        payload = record.payload
        for src, dst in (
            ("execution_id", "execution_id"),
            ("model", "model"),
            ("thread_id", "thread_id"),
            ("root", "root_id"),
            ("root_id", "root_id"),
            ("topic", "topic"),
        ):
            if getattr(row, dst) is None and payload.get(src):
                setattr(row, dst, str(payload[src]))
        if row.chat_url is None:
            for src in ("chat_url", "cse_chat_url"):
                if payload.get(src):
                    row.chat_url = normalize_chat_url(str(payload[src]))
                    break
        if row.thread_id and row.root_id is None:
            row.root_id = self._index.root_for_thread(row.thread_id)
        if row.root_id:
            self._index.link_cdp_leg(rid, row.root_id)
        apply_pending_chat(self, row)
        return row

    def _on_chat_bind(self, record: EventRecord) -> None:
        """Stamp-only CSE chat URL — never opens a leg row."""
        stash_or_stamp_chat(self, record)

    def _on_poll_hint(self, record: EventRecord) -> None:
        """Earliest G3 marker; non-CDP hints are ignored, not folded (v3 §6.3)."""
        payload = record.payload
        if str(payload.get("reply_from_agent", "")) != CDP_REPLY_FROM:
            return
        row = self._state(record)
        if row is None:
            return
        caller = payload.get("caller_agent")
        if caller:
            row.caller_agent = str(caller)
        thread = payload.get("thread_id")
        if thread and row.thread_id is None:
            row.thread_id = str(thread)
        if row.state == "unknown":
            row.state = "hint_issued"
        if row.hint_issued_ms is None or record.ts_unix_ms < row.hint_issued_ms:
            row.hint_issued_ms = record.ts_unix_ms

    def _on_admitted(self, record: EventRecord) -> None:
        """Open a leg row and start the elapsed clock (v3 §6)."""
        row = self._state(record)
        if row is None:
            return
        if row.admitted_at_ms is None or record.ts_unix_ms < row.admitted_at_ms:
            row.admitted_at_ms = record.ts_unix_ms
        if row.terminal_ms is None:
            row.state = "admitted"

    def _on_submitted(self, record: EventRecord) -> None:
        """Back-fill ``satellite_execution_id`` — not a progress milestone (§6.2)."""
        row = self._state(record)
        if row is None:
            return
        sat = record.payload.get("satellite_execution_id")
        if sat:
            row.satellite_execution_id = str(sat)

    def _on_proof(self, record: EventRecord) -> None:
        """Terminal success with harvest proof (v3 §6). Idempotent: first terminal wins."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        payload = record.payload
        row.terminal_ms = record.ts_unix_ms
        row.state = "proof"
        for src, dst in (
            ("archive_uri", "archive_uri"),
            ("content_proof_uri", "content_proof_uri"),
        ):
            if payload.get(src):
                setattr(row, dst, str(payload[src]))

    def _on_stalled(self, record: EventRecord) -> None:
        """Terminal failed without proof (v3 §6)."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        payload = record.payload
        row.terminal_ms = record.ts_unix_ms
        row.state = "stalled"
        stage = payload.get("stall_stage")
        if stage:
            row.stall_stage = str(stage)
        for key in ("error", "failure_reason"):
            if payload.get(key):
                row.failure_reason = str(payload[key])
                break

    def _on_delivery_failed(self, record: EventRecord) -> None:
        """Highest-severity terminal — harvest ok, bus post exhausted (v3 §6)."""
        row = self._state(record)
        if row is None or row.terminal_ms is not None:
            return
        payload = record.payload
        row.terminal_ms = record.ts_unix_ms
        row.state = "delivery_failed"
        stage = payload.get("stall_stage")
        if stage:
            row.stall_stage = str(stage)
        row.failure_reason = "on-behalf bus delivery exhausted"
