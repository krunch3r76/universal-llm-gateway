"""Read-only cursor dispatch ledger projections for HTTP routes."""

from __future__ import annotations

import json
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_closeout.conductor_park_harvest import (
    consult_pending_continue_owed,
)
from services.git_integration_worker.cursor_sdk_ledger_hop import (
    hop_fields_from_record_json,
)


def _is_conductor_row(row: dict[str, Any]) -> bool:
    from services.git_integration_worker.cursor_sdk_conductor_identity import (
        is_conductor_dispatch_row,
    )

    return is_conductor_dispatch_row(row)


def _record_json_dict(row: dict[str, Any]) -> dict[str, Any]:
    raw = str(row.get("record_json") or "")
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def latest_terminal_conductor_row_for_thread(
    ledger: CursorDispatchLedger,
    thread_id: str,
) -> dict[str, Any] | None:
    """Return the latest terminal conductor row on *thread_id*, or None."""
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


def latest_terminal_conductor_api_payload(
    *,
    thread_id: str,
    ledger: CursorDispatchLedger | None = None,
) -> dict[str, Any] | None:
    """Shape a terminal conductor row for ``GET .../latest-terminal-conductor``."""
    ledger = ledger or CursorDispatchLedger.instance()
    row = latest_terminal_conductor_row_for_thread(ledger, thread_id)
    if row is None:
        return None
    rec = _record_json_dict(row)
    hop = hop_fields_from_record_json(str(row.get("record_json") or ""))
    closeout_body = rec.get("closeout_body") or row.get("closeout_body") or ""
    if not isinstance(closeout_body, str):
        closeout_body = str(closeout_body)
    scoreboard_uri = str(
        rec.get("scoreboard_uri") or rec.get("scoreboard") or ""
    ).strip()
    closeout_turn = rec.get("closeout_turn")
    owed = consult_pending_continue_owed(row)
    return {
        "dispatch_id": row.get("dispatch_id"),
        "status": row.get("status"),
        "thread_id": row.get("thread_id"),
        "hop_seq": hop.get("hop_seq"),
        "record_json": rec,
        "closeout_body": closeout_body,
        "scoreboard_uri": scoreboard_uri,
        "closeout_turn": closeout_turn,
        "consult_pending_continue_owed": owed,
    }


__all__ = [
    "latest_terminal_conductor_api_payload",
    "latest_terminal_conductor_row_for_thread",
]
