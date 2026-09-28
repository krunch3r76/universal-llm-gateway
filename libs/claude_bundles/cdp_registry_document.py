"""Build the fleet registry HTTP document from a local active map."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cdp_registry.models import _RESERVED_STATUSES, STATUS_DORMANT

_VOCABULARY_STATUSES = _RESERVED_STATUSES | {STATUS_DORMANT}

SEAT_FIELD_SCHEMA = 1


def _optional_iso_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat()


def _seat_axis_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    execution_raw = record.get("execution_id")
    execution_id: str | None
    if execution_raw is None:
        execution_id = None
    else:
        text = str(execution_raw).strip()
        execution_id = text or None
    lane_raw = record.get("seat_lane")
    seat_lane: str | None
    if lane_raw is None:
        seat_lane = None
    else:
        text = str(lane_raw).strip()
        seat_lane = text or None
    return {
        "execution_id": execution_id,
        "seat_lane": seat_lane,
        "seat_bound_at": _optional_iso_at(record.get("seat_bound_at")),
        "seat_closed_at": _optional_iso_at(record.get("seat_closed_at")),
    }


def registry_document_supports_seat_axis(doc: Mapping[str, Any]) -> bool:
    """True when the document carries seat-axis fields (not a legacy vocabulary-only doc)."""
    if str(doc.get("availability") or "") != "ok":
        return False
    return int(doc.get("seat_field_schema") or 0) == SEAT_FIELD_SCHEMA


def build_registry_document(active: Mapping[str, Any]) -> dict[str, Any]:
    """Filter vocabulary rows and return the registry document shape."""
    observed_at = datetime.now(UTC).isoformat()
    blank_omitted = 0
    unknown_omitted = 0
    seats: list[dict[str, Any]] = []

    if not isinstance(active, Mapping):
        active = {}

    for key, record in active.items():
        if not isinstance(record, Mapping):
            continue
        status_raw = record.get("status")
        if status_raw is None or status_raw == "":
            blank_omitted += 1
            continue
        status = str(status_raw)
        if status not in _VOCABULARY_STATUSES:
            unknown_omitted += 1
            continue
        registration_id = str(record.get("registration_id") or key or "").strip()
        if not registration_id:
            # Vocabulary row with no registration_id and empty map key — omit silently.
            continue
        started = record.get("started_at")
        started_at: float | None
        if started is None:
            started_at = None
        else:
            try:
                started_at = float(started)
            except (TypeError, ValueError):
                started_at = None
        parent = record.get("parent_thread")
        purpose = record.get("purpose")
        seat_row: dict[str, Any] = {
            "registration_id": registration_id,
            "status": status,
            "started_at": started_at,
            "parent_thread": str(parent) if parent is not None else None,
            "purpose": str(purpose) if purpose is not None else None,
        }
        seat_row.update(_seat_axis_fields(record))
        seats.append(seat_row)

    seats.sort(key=lambda row: row["registration_id"])
    return {
        "availability": "ok",
        "observed_at": observed_at,
        "seat_field_schema": SEAT_FIELD_SCHEMA,
        "seat_count": len(seats),
        "blank_status_omitted": blank_omitted,
        "unknown_status_omitted": unknown_omitted,
        "seats": seats,
    }


def registry_unavailable_document() -> dict[str, Any]:
    """HTTP 200 body when the local active map cannot be loaded."""
    return {
        "availability": "unavailable",
        "observed_at": datetime.now(UTC).isoformat(),
        "seat_count": None,
        "blank_status_omitted": None,
        "unknown_status_omitted": None,
        "seats": None,
    }
