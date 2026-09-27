"""Build the fleet registry HTTP document from a local active map."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cdp_registry.models import _RESERVED_STATUSES, STATUS_DORMANT

_VOCABULARY_STATUSES = _RESERVED_STATUSES | {STATUS_DORMANT}


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
        seats.append(
            {
                "registration_id": registration_id,
                "status": status,
                "started_at": started_at,
                "parent_thread": str(parent) if parent is not None else None,
                "purpose": str(purpose) if purpose is not None else None,
            }
        )

    seats.sort(key=lambda row: row["registration_id"])
    return {
        "availability": "ok",
        "observed_at": observed_at,
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
