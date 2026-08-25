"""SdkFold state + row-key helpers (kept separate so ``sdk.py`` stays ≤400 SLOC)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
        "implement_gate_bypass",
        "lease_released_without_terminal",
        "closeout_uri",
        "pre_park_state",
        "last_tool_name",
        "last_tool_status",
        "tool_call_count",
        "seen_tool_call_ids",
        "parent_execution_id",
        "review_child",
        "admitted_via",
        "asked_by",
        "purpose",
        "story_id",
        "topic",
        "nest_under",
        "resume_of",
        "mcp_seat_class",
        "mcp_surface",
        "caller_from",
        "caller_via",
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
        self.implement_gate_bypass = False
        self.lease_released_without_terminal = False
        self.closeout_uri: str | None = None
        self.pre_park_state: str | None = None
        self.last_tool_name: str | None = None
        self.last_tool_status: str | None = None
        self.tool_call_count: int | None = None
        #: Distinct ``call_id`` values from ``worker.toolcall`` — live count source.
        self.seen_tool_call_ids: set[str] = set()
        self.parent_execution_id: str | None = None
        self.review_child = False
        self.admitted_via: str | None = None
        self.asked_by: str | None = None
        self.purpose: str | None = None
        self.story_id: str | None = None
        self.topic: str | None = None
        self.nest_under: str | None = None
        self.resume_of: str | None = None
        self.mcp_seat_class: str | None = None
        self.mcp_surface: str | None = None
        self.caller_from: str | None = None
        self.caller_via: str | None = None


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


def payload_alt_ids(
    payload: Mapping[str, Any],
    record: EventRecord,
    *,
    queued: bool = False,
) -> tuple[str | None, tuple[str, ...]]:
    """Return preferred row key and every identity id present on the payload."""
    preferred = (
        queued_dispatch_id(payload, record) if queued else dispatch_id(payload, record)
    )
    keys: tuple[str, ...] = (
        ("dispatch_id", "execution_id", "request_id", "worker_id")
        if queued
        else ("dispatch_id", "execution_id", "worker_id")
    )
    ids: list[str] = []
    seen: set[str] = set()
    for key in keys:
        value = payload.get(key)
        if value:
            token = str(value)
            if token not in seen:
                ids.append(token)
                seen.add(token)
    subject = envelope_subject(record)
    if subject and subject not in seen:
        ids.append(subject)
    return preferred, tuple(ids)


class SdkIdAliases:
    """``alt_id → canonical_id`` redirects for execution_id / dispatch_id splits."""

    __slots__ = ("_map",)

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def resolve(self, row_id: str) -> str:
        """Follow alias chain to the canonical row key."""
        seen: set[str] = set()
        current = row_id
        while current in self._map:
            if current in seen:
                break
            seen.add(current)
            current = self._map[current]
        return current

    def register(self, alt_id: str, canonical_id: str) -> None:
        """Record ``alt_id`` as an alias of ``canonical_id``."""
        canonical = self.resolve(canonical_id)
        if alt_id == canonical:
            return
        self._map[alt_id] = canonical
        resolved_alt = self.resolve(alt_id)
        if resolved_alt != canonical:
            self._map[resolved_alt] = canonical


def merge_sdk_state(canonical: SdkState, alt: SdkState) -> None:
    """Fold a live alt-keyed row into the preferred canonical row."""
    for field in (
        "root_id",
        "thread_id",
        "seat",
        "role",
        "model",
        "contract",
        "source_repo",
        "closeout_uri",
        "last_tool_name",
        "last_tool_status",
        "stall_stage",
        "pre_park_state",
        "failure_reason",
        "terminal_emitter",
        "parent_execution_id",
        "review_child",
        "admitted_via",
        "asked_by",
        "purpose",
        "story_id",
        "topic",
        "nest_under",
        "resume_of",
        "mcp_seat_class",
        "mcp_surface",
        "caller_from",
        "caller_via",
    ):
        if getattr(canonical, field) is None and getattr(alt, field) is not None:
            setattr(canonical, field, getattr(alt, field))

    if alt.started_ms is not None and (
        canonical.started_ms is None or alt.started_ms < canonical.started_ms
    ):
        canonical.started_ms = alt.started_ms

    if alt.last_progress_ms is not None and (
        canonical.last_progress_ms is None
        or alt.last_progress_ms > canonical.last_progress_ms
    ):
        canonical.last_progress_ms = alt.last_progress_ms

    absorb_tool_call_count(canonical, {"tool_call_count": alt.tool_call_count})
    canonical.seen_tool_call_ids |= alt.seen_tool_call_ids
    seen_n = len(canonical.seen_tool_call_ids)
    floor = canonical.tool_call_count or 0
    if seen_n > floor:
        canonical.tool_call_count = max(floor, seen_n)

    for emitter in alt.emitters_seen:
        if emitter not in canonical.emitters_seen:
            canonical.emitters_seen.append(emitter)

    for field in ("prompt_tokens", "completion_tokens", "cached_tokens"):
        alt_val = getattr(alt, field)
        if alt_val is not None:
            cur = getattr(canonical, field)
            if cur is None or alt_val > cur:
                setattr(canonical, field, alt_val)

    if alt.queue_position is not None and canonical.queue_position is None:
        canonical.queue_position = alt.queue_position

    if alt.delivery_failed:
        canonical.delivery_failed = True

    if alt.implement_gate_bypass:
        canonical.implement_gate_bypass = True

    if alt.lease_released_without_terminal:
        canonical.lease_released_without_terminal = True

    if canonical.parent_execution_id is None and alt.parent_execution_id is not None:
        canonical.parent_execution_id = alt.parent_execution_id
    if alt.review_child:
        canonical.review_child = True

    rank = {"unknown": 0, "queued": 1, "parked_waiting": 2, "running": 3}
    if rank.get(alt.state, 0) > rank.get(canonical.state, 0):
        canonical.state = alt.state

    if alt.provenance == "signal":
        canonical.provenance = "signal"

    for name in alt.divergent_fields:
        if name not in canonical.divergent_fields:
            canonical.divergent_fields.append(name)


def ensure_canonical_row(
    dispatches: dict[str, SdkState],
    aliases: SdkIdAliases,
    preferred_id: str,
    payload_ids: tuple[str, ...],
) -> SdkState:
    """Merge live alt-keyed rows into ``preferred_id``; register alias redirects."""
    preferred_id = aliases.resolve(preferred_id)
    resolved = tuple(
        dict.fromkeys(aliases.resolve(token) for token in (preferred_id, *payload_ids))
    )
    row = dispatches.get(preferred_id)
    for alt_id in resolved:
        if alt_id == preferred_id:
            continue
        alt_row = dispatches.get(alt_id)
        if alt_row is None or alt_row.terminal_ms is not None:
            continue
        if row is None:
            row = SdkState(preferred_id)
            dispatches[preferred_id] = row
        merge_sdk_state(row, alt_row)
        del dispatches[alt_id]
        aliases.register(alt_id, preferred_id)
    for token in payload_ids:
        aliases.register(token, preferred_id)
    if row is None:
        row = SdkState(preferred_id)
        dispatches[preferred_id] = row
    return row


def terminalize_id_siblings(
    dispatches: dict[str, SdkState],
    aliases: SdkIdAliases,
    primary: SdkState,
    payload: Mapping[str, Any],
    ts_unix_ms: int,
    *,
    state: str,
    failure_reason: str | None,
    emitter: str,
) -> None:
    """Close alternate-id ghost rows that never merged during the live path."""
    alt_ids: list[str] = []
    for key in ("dispatch_id", "execution_id", "worker_id"):
        value = payload.get(key)
        if value and str(value) != primary.dispatch_id:
            alt_ids.append(str(value))
    for alt_id in alt_ids:
        alt_id = aliases.resolve(alt_id)
        if alt_id == primary.dispatch_id:
            continue
        sibling = dispatches.get(alt_id)
        if sibling is None or sibling.terminal_ms is not None:
            continue
        if sibling.last_progress_ms is None or ts_unix_ms > sibling.last_progress_ms:
            sibling.last_progress_ms = ts_unix_ms
        sibling.terminal_ms = ts_unix_ms
        sibling.terminal_emitter = emitter
        sibling.state = state
        if failure_reason:
            sibling.failure_reason = failure_reason
