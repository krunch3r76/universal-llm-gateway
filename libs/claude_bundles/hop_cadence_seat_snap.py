"""Join CSE-registry seats into the hop-cadence identity snap.

``GET /v1/project-ask/active-work`` is execution-store-only: a seated operator
CSE with no in-flight project-ask has no ``rows`` entry. Occupancy on
drain-state is counts-only (no ``parent_thread`` / ``purpose``). The CSE
session registry is the identity-bearing seat source. This module unions
registry seats onto the snap that both predecessor capture and
``refuse_cadence_hop_for_live_seat`` already share, without mutating admission
scalars (``running_count``, ``free_slots``). X occupancy is a separate
active-work attach (``x_*``), not this union.
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


def seated_row_from_registry_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project one listable registry row into a hop-identity snap row.

    Listable statuses mean a Chrome process may still hold the CSE. Empty
    ``execution_id`` becomes ``SEATED_NO_STREAM_EXECUTION`` so incumbency
    filters that require a nonempty execution id still see the seat. Status
    is synthesized as ``running`` for identity consumers; admission scalars
    are not derived from these rows.
    """
    status = str(record.get("status") or "")
    if status not in _HOST_LISTABLE_STATUSES:
        return None
    registration_id = str(record.get("registration_id") or "").strip()
    if not registration_id:
        return None
    execution_id = str(record.get("execution_id") or "").strip()
    return {
        "registration_id": registration_id,
        "execution_id": execution_id or SEATED_NO_STREAM_EXECUTION,
        "parent_thread": record.get("parent_thread"),
        "purpose": record.get("purpose"),
        "status": "running",
        "source": SEATED_SOURCE,
    }


def seated_rows_from_registry_records(
    records: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project listable registry records into hop-identity seated rows for capture and refuse."""
    values: list[Mapping[str, Any]]
    if isinstance(records, Mapping):
        values = [row for row in records.values() if isinstance(row, Mapping)]
    else:
        values = [row for row in records if isinstance(row, Mapping)]
    out: list[dict[str, Any]] = []
    for record in values:
        projected = seated_row_from_registry_record(record)
        if projected is not None:
            out.append(projected)
    return out


def read_registry_seated_rows(
    *,
    load_active: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
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
    return seated_rows_from_registry_records(raw)


def seat_row_from_registry_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project one seat-open registry row into a hop-identity snap row.

    ``seat_open`` is status-independent: dormant driving seats count here.
    Empty ``execution_id`` becomes ``SEATED_NO_STREAM_EXECUTION``. Status
    is synthesized as ``running`` for identity consumers; ``host_status``
    carries the real registry status for hygiene only.
    """
    if not seat_open(record):
        return None
    registration_id = str(record.get("registration_id") or "").strip()
    if not registration_id:
        return None
    execution_id = str(record.get("execution_id") or "").strip()
    host_status = str(record.get("status") or "")
    return {
        "registration_id": registration_id,
        "execution_id": execution_id or SEATED_NO_STREAM_EXECUTION,
        "parent_thread": record.get("parent_thread"),
        "purpose": record.get("purpose"),
        "status": "running",
        "source": SEAT_SOURCE,
        "seat": True,
        "host_status": host_status,
    }


def seat_rows_from_registry_records(
    records: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project seat-open registry records into hop-identity seat-axis rows."""
    values: list[Mapping[str, Any]]
    if isinstance(records, Mapping):
        values = [row for row in records.values() if isinstance(row, Mapping)]
    else:
        values = [row for row in records if isinstance(row, Mapping)]
    out: list[dict[str, Any]] = []
    for record in values:
        projected = seat_row_from_registry_record(record)
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
    try:
        from claude_bundles.cdp_registry_store import load_active as _load_active

        raw = _load_active()
    except Exception:  # noqa: BLE001 — hop identity must fail open to store rows
        raw = {}
    if not isinstance(raw, Mapping):
        raw = {}
    out = snap
    if need_seated:
        out = attach_seated_rows(out, seated_rows_from_registry_records(raw))
    if need_seat:
        out = attach_seat_rows(out, seat_rows_from_registry_records(raw))
    return out


def identity_rows(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Union execution-store rows with seated rows; store rows win per registration.

    Capacity keeps reading snap scalars / ``rows``. Identity questions
    (who is seated) — including request-admission census — read this union.
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
