"""Join CSE-registry seats into the hop-cadence identity snap.

``GET /v1/project-ask/active-work`` is execution-store-only: a seated operator
CSE with no in-flight project-ask has no ``rows`` entry. Occupancy on
drain-state is counts-only (no ``parent_thread`` / ``purpose``). The CSE
session registry is the identity-bearing seat source. This module unions
registry seats onto the snap that both predecessor capture and
``refuse_cadence_hop_for_live_seat`` already share, without mutating admission
scalars (``running_count``, ``free_slots``). X occupancy is a separate
active-work attach (``x_*``), not this union.

Identity union field guide (R2′):

- ``rows`` (execution store): ``stream_state`` is authoritative for stream
  liveness; admission scalars still read ``status`` on store rows only.
- ``seated_rows`` / ``seat_rows`` (registry): ``seat_state`` is registry
  status; ``stream_state`` joins the execution store / inflight leg for the
  registration's current ``execution_id``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from claude_bundles.cdp_registry.models import _HOST_LISTABLE_STATUSES, seat_open

SEATED_NO_STREAM_EXECUTION = "__none:seated_no_stream__"
SEATED_ROWS_KEY = "seated_rows"
SEAT_ROWS_KEY = "seat_rows"
SEATED_SOURCE = "cse-session-registry"
SEAT_SOURCE = "cse-session-registry:seat-axis"

STREAM_NONE = "none"
STREAM_PENDING = "pending"
STREAM_RUNNING = "running"
_LIVE_STREAM_STATES = frozenset({STREAM_PENDING, STREAM_RUNNING})
_TERMINAL_EXECUTION_STATUSES = frozenset({"completed", "failed", "aborted"})

StreamIndex = dict[str, str]


def is_live_stream_state(stream_state: str | None) -> bool:
    """True when ``stream_state`` is a pending or running execution stream."""
    return str(stream_state or "") in _LIVE_STREAM_STATES


def stream_state_terminal(execution_id: str) -> str:
    """Format a terminal stream token bound to ``execution_id``."""
    return f"terminal:{execution_id}"


def merge_stream_index(*indexes: Mapping[str, str] | None) -> StreamIndex:
    """Merge execution-id → status maps; later indexes override earlier keys."""
    out: StreamIndex = {}
    for index in indexes:
        if not index:
            continue
        for key, value in index.items():
            token = str(key or "").strip()
            if token:
                out[token] = str(value or "")
    return out


def build_stream_index_from_snap(snap: Mapping[str, Any]) -> StreamIndex:
    """Build a join index from snap ``rows`` and optional ``execution_streams``."""
    rows = snap.get("rows") if isinstance(snap.get("rows"), list) else []
    store_rows = [row for row in rows if isinstance(row, dict)]
    raw_streams = snap.get("execution_streams")
    extra: StreamIndex = {}
    if isinstance(raw_streams, Mapping):
        extra = {
            str(k): str(v)
            for k, v in raw_streams.items()
            if str(k or "").strip()
        }
    return merge_stream_index(
        build_stream_index_from_rows(store_rows),
        extra,
    )


def build_stream_index_from_rows(rows: list[Mapping[str, Any]]) -> StreamIndex:
    """Derive execution-id → status from projected identity/store rows."""
    index: StreamIndex = {}
    for row in rows:
        exec_id = str(row.get("execution_id") or "").strip()
        if not exec_id or exec_id == SEATED_NO_STREAM_EXECUTION:
            continue
        stream = str(row.get("stream_state") or "")
        if stream.startswith("terminal:"):
            index[exec_id] = "failed"
            continue
        if stream in _LIVE_STREAM_STATES:
            index[exec_id] = stream
            continue
        status = str(row.get("status") or "")
        if status:
            index[exec_id] = status
    return index


def project_stream_state(
    execution_id: str,
    *,
    stream_index: StreamIndex | None = None,
) -> str:
    """Map an execution id to ``none`` | ``pending`` | ``running`` | ``terminal:<id>``."""
    token = str(execution_id or "").strip()
    if not token or token == SEATED_NO_STREAM_EXECUTION:
        return STREAM_NONE
    raw = (stream_index or {}).get(token)
    if raw in _LIVE_STREAM_STATES:
        return raw
    if raw in _TERMINAL_EXECUTION_STATUSES:
        return stream_state_terminal(token)
    return STREAM_NONE


def seated_row_from_registry_record(
    record: Mapping[str, Any],
    *,
    stream_index: StreamIndex | None = None,
) -> dict[str, Any] | None:
    """Project one listable registry row into a hop-identity snap row.

    Listable statuses mean a Chrome process may still hold the CSE. Empty
    ``execution_id`` becomes ``SEATED_NO_STREAM_EXECUTION`` so incumbency
    filters that require a nonempty execution id still see the seat.
    ``seat_state`` carries registry status; ``stream_state`` joins the
    execution store for the registration's current execution.
    """
    status = str(record.get("status") or "")
    if status not in _HOST_LISTABLE_STATUSES:
        return None
    registration_id = str(record.get("registration_id") or "").strip()
    if not registration_id:
        return None
    execution_id = str(record.get("execution_id") or "").strip()
    exec_for_row = execution_id or SEATED_NO_STREAM_EXECUTION
    return {
        "registration_id": registration_id,
        "execution_id": exec_for_row,
        "parent_thread": record.get("parent_thread"),
        "purpose": record.get("purpose"),
        "seat_state": status,
        "stream_state": project_stream_state(exec_for_row, stream_index=stream_index),
        "source": SEATED_SOURCE,
    }


def seated_rows_from_registry_records(
    records: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]],
    *,
    stream_index: StreamIndex | None = None,
) -> list[dict[str, Any]]:
    """Project listable registry records into hop-identity seated rows for capture and refuse."""
    values: list[Mapping[str, Any]]
    if isinstance(records, Mapping):
        values = [row for row in records.values() if isinstance(row, Mapping)]
    else:
        values = [row for row in records if isinstance(row, Mapping)]
    out: list[dict[str, Any]] = []
    for record in values:
        projected = seated_row_from_registry_record(
            record, stream_index=stream_index
        )
        if projected is not None:
            out.append(projected)
    return out


def read_registry_seated_rows(
    *,
    load_active: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
    stream_index: StreamIndex | None = None,
) -> list[dict[str, Any]]:
    """Load listable CSE-registry seats as hop-identity rows; empty on I/O fault."""
    try:
        if load_active is None:
            from claude_bundles.cdp_registry_store import load_active as _load_active

            raw = _load_active()
        else:
            raw = load_active()
    except Exception:  # noqa: BLE001 — hop identity must fail open to store rows
        return []
    if not isinstance(raw, Mapping):
        return []
    return seated_rows_from_registry_records(raw, stream_index=stream_index)


def seat_row_from_registry_record(
    record: Mapping[str, Any],
    *,
    stream_index: StreamIndex | None = None,
) -> dict[str, Any] | None:
    """Project one seat-open registry row into a hop-identity snap row.

    ``seat_open`` is status-independent: dormant driving seats count here.
    Empty ``execution_id`` becomes ``SEATED_NO_STREAM_EXECUTION``.
    ``seat_state`` carries registry status; ``stream_state`` joins the store.
    """
    if not seat_open(record):
        return None
    registration_id = str(record.get("registration_id") or "").strip()
    if not registration_id:
        return None
    execution_id = str(record.get("execution_id") or "").strip()
    exec_for_row = execution_id or SEATED_NO_STREAM_EXECUTION
    host_status = str(record.get("status") or "")
    return {
        "registration_id": registration_id,
        "execution_id": exec_for_row,
        "parent_thread": record.get("parent_thread"),
        "purpose": record.get("purpose"),
        "seat_state": host_status,
        "stream_state": project_stream_state(exec_for_row, stream_index=stream_index),
        "source": SEAT_SOURCE,
        "seat": True,
        "host_status": host_status,
    }


def seat_rows_from_registry_records(
    records: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]],
    *,
    stream_index: StreamIndex | None = None,
) -> list[dict[str, Any]]:
    """Project seat-open registry records into hop-identity seat-axis rows."""
    values: list[Mapping[str, Any]]
    if isinstance(records, Mapping):
        values = [row for row in records.values() if isinstance(row, Mapping)]
    else:
        values = [row for row in records if isinstance(row, Mapping)]
    out: list[dict[str, Any]] = []
    for record in values:
        projected = seat_row_from_registry_record(
            record, stream_index=stream_index
        )
        if projected is not None:
            out.append(projected)
    return out


def attach_seated_rows(
    snap: dict[str, Any],
    seated_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Copy ``snap`` and set ``seated_rows`` without rewriting admission ``rows`` or scalars."""
    out = dict(snap)
    out[SEATED_ROWS_KEY] = list(seated_rows)
    return out


def attach_seat_rows(
    snap: dict[str, Any],
    seat_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Copy ``snap`` and set ``seat_rows`` without rewriting admission ``rows`` or scalars."""
    out = dict(snap)
    out[SEAT_ROWS_KEY] = list(seat_rows)
    return out


def attach_registry_seated_rows(snap: dict[str, Any]) -> dict[str, Any]:
    """Attach live registry seats when the caller has not already injected them."""
    need_seated = not isinstance(snap.get(SEATED_ROWS_KEY), list)
    need_seat = not isinstance(snap.get(SEAT_ROWS_KEY), list)
    if not need_seated and not need_seat:
        return snap
    stream_index = build_stream_index_from_snap(snap)
    try:
        from claude_bundles.cdp_registry_store import load_active as _load_active

        raw = _load_active()
    except Exception:  # noqa: BLE001 — hop identity must fail open to store rows
        raw = {}
    if not isinstance(raw, Mapping):
        raw = {}
    out = snap
    if need_seated:
        out = attach_seated_rows(
            out,
            seated_rows_from_registry_records(raw, stream_index=stream_index),
        )
    if need_seat:
        out = attach_seat_rows(
            out,
            seat_rows_from_registry_records(raw, stream_index=stream_index),
        )
    return out


def identity_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Union execution-store rows with seated rows; store rows win per registration.

    Capacity keeps reading snap scalars / ``rows``. Identity questions
    (who is seated) — including request-admission census — read this union.

    Field use by leg:
    - execution-store ``rows``: ``stream_state`` (``seat_state`` unset)
    - ``seated_rows`` / ``seat_rows``: ``seat_state`` + ``stream_state``
    """
    store = [
        row
        for row in (snap.get("rows") if isinstance(snap.get("rows"), list) else [])
        if isinstance(row, dict)
    ]
    seated = [
        row
        for row in (
            snap.get(SEATED_ROWS_KEY)
            if isinstance(snap.get(SEATED_ROWS_KEY), list)
            else []
        )
        if isinstance(row, dict)
    ]
    seat_leg = [
        row
        for row in (
            snap.get(SEAT_ROWS_KEY)
            if isinstance(snap.get(SEAT_ROWS_KEY), list)
            else []
        )
        if isinstance(row, dict)
    ]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in store:
        registration_id = str(row.get("registration_id") or "").strip()
        if registration_id:
            seen.add(registration_id)
        out.append(row)
    for row in seated:
        registration_id = str(row.get("registration_id") or "").strip()
        if not registration_id or registration_id in seen:
            continue
        seen.add(registration_id)
        out.append(row)
    for row in seat_leg:
        registration_id = str(row.get("registration_id") or "").strip()
        if not registration_id or registration_id in seen:
            continue
        seen.add(registration_id)
        out.append(row)
    return out
