"""SdkFold state + row-key helpers (kept separate so ``sdk.py`` stays ≤400 SLOC)."""

from __future__ import annotations

from typing import Any, Mapping

from ..protocols import EventRecord, envelope_subject


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
        "last_tool_name",
        "last_tool_status",
        "tool_call_count",
        "seen_tool_call_ids",
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
        self.last_tool_name: str | None = None
        self.last_tool_status: str | None = None
        self.tool_call_count: int | None = None
        #: Distinct ``call_id`` values from ``worker.toolcall`` — live count source.
        self.seen_tool_call_ids: set[str] = set()


def as_int(value: Any) -> int | None:
    """Coerce ``value`` to ``int``, returning ``None`` when it is not numeric."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def absorb_tool_call_count(row: SdkState, payload: Mapping[str, Any]) -> None:
    """Take monotonic max of ``tool_call_count`` from progress/completed payloads."""
    count = as_int(payload.get("tool_call_count"))
    if count is None:
        return
    if row.tool_call_count is None or count > row.tool_call_count:
        row.tool_call_count = count


def note_tool_call_id(row: SdkState, call_id: str | None) -> None:
    """Raise live ``tool_call_count`` from distinct ``worker.toolcall`` call ids.

    Progress heartbeats only refresh every ~30s; toolcall events fire per call.
    Reconnect-safe: set membership, not blind increment. A new id bumps past the
    progress floor (``max(current+1, |seen|)``).
    """
    if not call_id:
        return
    before = len(row.seen_tool_call_ids)
    row.seen_tool_call_ids.add(str(call_id))
    n = len(row.seen_tool_call_ids)
    if n == before:
        return
    base = row.tool_call_count or 0
    row.tool_call_count = max(base + 1, n)


def first_str(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    """Return the first truthy value among ``keys`` as a string, else ``None``."""
    for key in keys:
        if payload.get(key):
            return str(payload[key])
    return None


def dispatch_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve the dispatch row key.

    Prefer ``dispatch_id`` over ``execution_id``. GIW ``worker.completed`` carries
    both with *different* values (cursor-sdk id vs run UUID); preferring
    ``execution_id`` split the fold into a live progress row and a sibling
    terminal row — zombies that looked "live" forever while terminals counted
    as a separate "done" population.
    """
    for key in ("dispatch_id", "execution_id", "worker_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return envelope_subject(record)


def queued_dispatch_id(payload: Mapping[str, Any], record: EventRecord) -> str | None:
    """Resolve dispatch key for ``worker.queued``.

    Prefer ``dispatch_id`` even on the stargate-shaped payload (request_id present,
    no source_repo). Preferring ``execution_id`` there minted a sibling ghost row
    that never saw the worker-lane terminal keyed on ``dispatch_id``.
    """
    origin = payload.get("origin_service")
    if origin == "stargate" or (
        payload.get("request_id") is not None and payload.get("source_repo") is None
    ):
        for key in ("dispatch_id", "execution_id", "request_id"):
            value = payload.get(key)
            if value:
                return str(value)
        return None
    return dispatch_id(payload, record)


def lease_row_id(payload: Mapping[str, Any], key: str) -> str | None:
    """Resolve a lease/park row id from ``parent_id`` / ``child_id`` / ``dispatch_id``."""
    value = payload.get(key)
    if value:
        return str(value)
    return None
