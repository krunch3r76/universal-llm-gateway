"""Work-key admission gate helpers (todo:cursor-sdk-dispatch-work-key-gate)."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from services.git_integration_worker.cursor_sdk_packet import is_valid_work_key_scheme

WORK_KEY_SCHEMES = (
    "todo:",
    "plan:",
    "plan_phase:",
    "packet:",
    "agent-bus:",
    "friction:",
    "decision:",
    "adhoc:",
)

_WRITE_CLASS_CONTRACTS = frozenset(
    {"implement", "conductor", "sketch", "pure-mechanical", "wrap"}
)

_GATE_MODE_DEFAULT = "observe"


def gate_mode() -> str:
    """``observe`` (default) or ``enforce`` — Gates 1/3/4 observe emits and continues."""
    raw = (
        os.environ.get("CURSOR_SDK_WORK_KEY_GATE_MODE") or _GATE_MODE_DEFAULT
    ).strip().lower()
    return raw if raw in ("observe", "enforce") else _GATE_MODE_DEFAULT


def work_key_seq_cap() -> int:
    try:
        return max(1, int(os.environ.get("CURSOR_SDK_WORK_KEY_SEQ_CAP", "8")))
    except ValueError:
        return 8


def validate_work_key_scheme(work_key: str) -> bool:
    return is_valid_work_key_scheme(work_key)


def compute_write_class(
    *,
    read_only: bool,
    lane: str | None,
    contract: str,
) -> bool:
    """D3: write-class admits require a declared work_key (or adhoc derivation)."""
    if lane == "B":
        return True
    if read_only:
        return False
    return contract.lower() in _WRITE_CLASS_CONTRACTS


def derive_adhoc_work_key(work_fingerprint: str) -> str:
    return f"adhoc:{work_fingerprint[:16]}"


def is_root_row(
    *,
    resume_of: str | None,
    nest_under: str | None,
    hop_from: str | None,
) -> bool:
    """A1: roots-only remint seq — all lineage carriers NULL."""
    return resume_of is None and nest_under is None and hop_from is None


def lineage_carriers_from_record(record_json: str | None) -> tuple[str | None, str | None]:
    """Read ``nest_under`` / ``hop_from`` from persisted ``record_json``."""
    if not record_json:
        return None, None
    try:
        data = json.loads(record_json)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    nest = data.get("nest_under")
    hop = data.get("hop_from")
    return (
        str(nest).strip() if nest else None,
        str(hop).strip() if hop else None,
    )


def row_is_root(
    *,
    resume_of: str | None,
    record_json: str | None,
    hop_from_column: str | None = None,
    nest_under_column: str | None = None,
) -> bool:
    nest_json, hop_json = lineage_carriers_from_record(record_json)
    return is_root_row(
        resume_of=resume_of,
        nest_under=nest_under_column or nest_json,
        hop_from=hop_from_column or hop_json,
    )


def lineage_depth_for_parent(
    conn: sqlite3.Connection,
    parent_dispatch_id: str,
) -> int:
    row = conn.execute(
        "SELECT lineage_depth FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (parent_dispatch_id,),
    ).fetchone()
    if row is None:
        return 0
    return int(row["lineage_depth"] or 0) + 1


def count_root_rows_24h(
    conn: sqlite3.Connection,
    *,
    work_key: str,
    exclude_dispatch_id: str,
) -> int:
    """Count root admits for ``work_key`` in the rolling 24 h window."""
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    rows = conn.execute(
        "SELECT dispatch_id, resume_of, record_json, hop_from, nest_under "
        "FROM cursor_sdk_dispatches "
        "WHERE work_key=? AND dispatch_id<>? "
        "AND COALESCE(started_at, queued_at, terminal_at) >= ?",
        (work_key, exclude_dispatch_id, cutoff),
    ).fetchall()
    count = 0
    for row in rows:
        if row_is_root(
            resume_of=row["resume_of"],
            record_json=row["record_json"],
            hop_from_column=row["hop_from"] if "hop_from" in row.keys() else None,
            nest_under_column=row["nest_under"] if "nest_under" in row.keys() else None,
        ):
            count += 1
    return count


def holder_kind_from_row(row: sqlite3.Row | dict[str, Any]) -> str:
    packet_kind = row["packet_kind"] if "packet_kind" in row.keys() else None
    if packet_kind == "conductor":
        return "conductor"
    contract = row["contract"] if "contract" in row.keys() else None
    if contract == "conductor":
        return "conductor"
    if contract == "implement":
        return "implement"
    return "other"


def steer_for_holder(
    *,
    holder_dispatch_id: str,
    holder_thread_id: str | None,
) -> dict[str, str]:
    """Return explicit lineage hints the caller may pass to admit under a holder."""
    steer: dict[str, str] = {}
    if holder_dispatch_id:
        steer["nest_under"] = holder_dispatch_id
        steer["resume_of"] = holder_dispatch_id
    if holder_thread_id:
        steer["reuse_thread"] = holder_thread_id
    return steer


def derive_work_identity(
    *,
    req_work_key: str | None,
    packet_work_key: str | None,
    source_ref: str | None,
    work_fingerprint: str | None,
    write_class: bool,
) -> tuple[str | None, str]:
    """Return ``(work_key, identity_class)`` after derivation order."""
    candidate = (req_work_key or packet_work_key or source_ref or "").strip() or None
    if candidate:
        if validate_work_key_scheme(candidate):
            return candidate, "declared"
        return None, "invalid"
    if not write_class and work_fingerprint:
        return derive_adhoc_work_key(work_fingerprint), "adhoc"
    return None, "missing"
