"""Conductor-open duplicate-driver gate for cursor-sdk admit."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenConductorHolder:
    """In-flight conductor dispatch holding a todo work identity."""

    dispatch_id: str
    thread_id: str | None
    work_key: str


def _record_packet_kind(record_json: str) -> str | None:
    try:
        data = json.loads(record_json or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("packet_kind")
    return str(kind).strip().lower() if kind else None


def find_open_conductor_holder_conn(
    conn: sqlite3.Connection,
    *,
    work_key: str,
    exclude_dispatch_id: str | None = None,
) -> OpenConductorHolder | None:
    """Return an open conductor row for ``work_key``, if any."""
    row = conn.execute(
        "SELECT dispatch_id, thread_id, work_key, record_json, contract "
        "FROM cursor_sdk_dispatches "
        "WHERE work_key=? AND status IN ('queued','admitted','running') "
        "AND dispatch_id<>COALESCE(?, '') "
        "LIMIT 1",
        (work_key, exclude_dispatch_id or ""),
    ).fetchone()
    if row is None:
        return None
    record_json = row["record_json"] if "record_json" in row.keys() else "{}"
    contract = str(row["contract"] or "").lower()
    packet_kind = _record_packet_kind(record_json or "")
    is_conductor = packet_kind == "conductor" or (
        contract == "light-bounded" and packet_kind == "conductor"
    )
    if not is_conductor and contract == "light-bounded":
        # Fallback: light-bounded rows with matching work_key are conductor sessions
        # once packet_kind is stamped at admit; until then treat as conductor when
        # contract is light-bounded and work_key is set (spawn wire shape).
        is_conductor = bool(row["work_key"])
    if not is_conductor:
        return None
    return OpenConductorHolder(
        dispatch_id=row["dispatch_id"],
        thread_id=row["thread_id"],
        work_key=str(row["work_key"] or work_key),
    )


def should_block_implement_for_open_conductor(
    *,
    contract: str,
    nest_under: str | None,
    work_key: str | None,
) -> bool:
    """True when top-level implement must 409 against an open conductor."""
    if contract != "implement":
        return False
    if nest_under:
        return False
    return bool(work_key)
