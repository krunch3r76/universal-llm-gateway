"""Read latest terminal conductor dispatch row for a worker thread."""

from __future__ import annotations

import json
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_hop import (
    _is_conductor_row,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)


def latest_terminal_conductor_for_thread(thread_id: str) -> dict[str, Any] | None:
    """Return the latest terminal conductor row on *thread_id*, or None."""
    ledger = CursorDispatchLedger.instance()
    best: dict[str, Any] | None = None
    best_seq = -1
    with ledger._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cursor_sdk_dispatches "
            "WHERE thread_id=? AND status IN ('completed','failed','cancelled')",
            (thread_id,),
        ).fetchall()
    for row in rows:
        mapped = {k: row[k] for k in row.keys()}
        if not _is_conductor_row(mapped):
            continue
        hop_seq = hop_fields_from_record_json(str(mapped.get("record_json") or "")).get(
            "hop_seq"
        )
        seq_i = int(hop_seq) if isinstance(hop_seq, int) else 0
        if seq_i >= best_seq:
            best_seq = seq_i
            best = mapped
    return best


def record_json_dict(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}
