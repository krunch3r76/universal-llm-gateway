"""Conductor hop lineage stamp at ledger admit (H2)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from universal_protocol.errors import ProtocolError

from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
    stamp_hop_on_record_json,
)

HOP_ADMITTED_BY_KEY = "hop_admitted_by"
_LINEAGE_MISMATCH_CODE = "CONDUCTOR_HOP_LINEAGE_MISMATCH"


@dataclass(frozen=True, slots=True)
class HopLineage:
    hop_from: str
    hop_seq: int
    hop_reason: str
    hop_admitted_by: str


def derive_hop_admitted_by(*, caller_agent: str | None, hop_reason: str) -> str:
    """Map admit caller + hop_reason to persisted ``hop_admitted_by``."""
    if hop_reason == "watchdog":
        return "watchdog"
    agent = str(caller_agent or "").lower()
    if "mcp" in agent:
        return "mcp"
    if agent in {"cursor", "web-anthropic", "dispatch", "liaison"} or "liaison" in agent:
        return "liaison"
    return "conductor-hop"


def _latest_terminal_predecessor(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    exclude_dispatch_id: str | None = None,
) -> dict[str, Any] | None:
    rows = conn.execute(
        "SELECT * FROM cursor_sdk_dispatches "
        "WHERE thread_id=? AND status IN ('completed','failed','cancelled') "
        "ORDER BY json_extract(record_json, '$.hop_seq') DESC, "
        "COALESCE(terminal_at, queued_at) DESC",
        (thread_id,),
    ).fetchall()
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if exclude_dispatch_id and mapped.get("dispatch_id") == exclude_dispatch_id:
            continue
        if not is_conductor_dispatch_row(mapped):
            continue
        hop_fields = hop_fields_from_record_json(str(mapped.get("record_json") or ""))
        if hop_fields.get("hop_successor"):
            continue
        return mapped
    return None


def derive_hop_lineage(
    conn: sqlite3.Connection,
    *,
    thread_id: str | None,
    work_key: str | None,
    caller_agent: str | None,
    body_triplet: dict[str, Any] | None = None,
    incoming_dispatch_id: str | None = None,
) -> HopLineage | None:
    """Derive successor hop lineage from latest terminal predecessor on thread."""
    _ = work_key
    if not thread_id:
        return None
    predecessor = _latest_terminal_predecessor(
        conn, thread_id=thread_id, exclude_dispatch_id=incoming_dispatch_id
    )
    if predecessor is None:
        return None
    pred_id = str(predecessor.get("dispatch_id") or "")
    if not pred_id:
        return None
    hop_fields = hop_fields_from_record_json(str(predecessor.get("record_json") or ""))
    prior_seq = hop_fields.get("hop_seq")
    next_seq = int(prior_seq) + 1 if isinstance(prior_seq, int) else 1
    body = body_triplet or {}
    hop_reason = str(body.get("hop_reason") or "planned")
    if hop_reason not in {"spawn", "planned", "crash", "silent", "watchdog", "park_harvest"}:
        hop_reason = "planned"
    admitted_by = derive_hop_admitted_by(caller_agent=caller_agent, hop_reason=hop_reason)
    lineage = HopLineage(
        hop_from=pred_id,
        hop_seq=next_seq,
        hop_reason=hop_reason,
        hop_admitted_by=admitted_by,
    )
    if body_triplet:
        for key, derived in (
            ("hop_from", lineage.hop_from),
            ("hop_seq", lineage.hop_seq),
            ("hop_reason", lineage.hop_reason),
        ):
            claimed = body_triplet.get(key)
            if claimed is not None and claimed != derived:
                raise ProtocolError(
                    code=_LINEAGE_MISMATCH_CODE,
                    message=f"hop {key} claim {claimed!r} mismatches derived {derived!r}",
                    source="git_integration_worker",
                    retryable=False,
                    data={"field": key, "claimed": claimed, "derived": derived},
                )
    return lineage


def apply_hop_lineage_stamp(
    conn: sqlite3.Connection,
    *,
    incoming_dispatch_id: str,
    thread_id: str,
    record_json: str,
    lineage: HopLineage,
) -> str:
    """Stamp successor + predecessor ``hop_successor`` in one admit transaction."""
    stamped = stamp_hop_on_record_json(
        record_json,
        hop_seq=lineage.hop_seq,
        hop_from=lineage.hop_from,
        hop_reason=lineage.hop_reason,
    )
    data = json.loads(stamped)
    if not isinstance(data, dict):
        data = {}
    data[HOP_ADMITTED_BY_KEY] = lineage.hop_admitted_by
    stamped = json.dumps(data, sort_keys=True, separators=(",", ":"))

    from services.git_integration_worker.cursor_sdk_ledger_hop import merge_hop_patch

    pred_row = conn.execute(
        "SELECT record_json FROM cursor_sdk_dispatches WHERE dispatch_id=?",
        (lineage.hop_from,),
    ).fetchone()
    if pred_row is not None:
        pred_json = merge_hop_patch(
            str(pred_row["record_json"] or ""),
            {"hop_successor": incoming_dispatch_id},
        )
        conn.execute(
            "UPDATE cursor_sdk_dispatches SET record_json=? WHERE dispatch_id=?",
            (pred_json, lineage.hop_from),
        )
    from services.git_integration_worker.cursor_sdk_hop_events import (
        emit_frontier_sdk_conductor_hop_lineage_stamped,
    )

    emit_frontier_sdk_conductor_hop_lineage_stamped(
        dispatch_id=incoming_dispatch_id,
        predecessor_dispatch_id=lineage.hop_from,
        thread_id=thread_id,
        hop_seq=lineage.hop_seq,
        hop_admitted_by=lineage.hop_admitted_by,
    )
    return stamped


__all__ = [
    "HOP_ADMITTED_BY_KEY",
    "HopLineage",
    "apply_hop_lineage_stamp",
    "derive_hop_admitted_by",
    "derive_hop_lineage",
]
