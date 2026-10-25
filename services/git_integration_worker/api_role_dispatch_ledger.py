"""Persist API-role team_dispatch economics onto the GIW work-key ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_usage_extract import (
    DispatchUsageRecord,
    persist_dispatch_usage,
)


def record_api_role_dispatch(
    *,
    execution_id: str,
    work_key: str | None,
    resolved_model: str,
    contract: str | None,
    usage: dict[str, Any] | None,
    usage_capture_status: str = "captured",
) -> None:
    """Insert or update a synthetic ledger row keyed by execution_id."""
    if not work_key:
        return
    ledger = CursorDispatchLedger.instance()
    dispatch_id = f"api-{execution_id}"
    stamp = datetime.now(UTC).isoformat()
    record_json = json.dumps(
        {
            "usage": usage,
            "usage_capture_status": usage_capture_status,
            "substrate": "api_role",
        }
    )
    with ledger._connect() as conn:
        existing = conn.execute(
            "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, execution_id, resolved_model, "
                "message_present, status, record_json, contract, work_key, queued_at, "
                "started_at, terminal_at, terminal_status) "
                "VALUES (?, ?, '', ?, ?, 0, 'completed', ?, ?, ?, ?, ?, ?, 'completed')",
                (
                    dispatch_id,
                    dispatch_id,
                    execution_id,
                    resolved_model,
                    record_json,
                    contract or "freeform",
                    work_key,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
        else:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET record_json=?, terminal_at=?, "
                "resolved_model=?, contract=? WHERE dispatch_id=?",
                (record_json, stamp, resolved_model, contract or "freeform", dispatch_id),
            )
        conn.commit()
    persist_dispatch_usage(
        ledger,
        dispatch_id=dispatch_id,
        record=DispatchUsageRecord(
            usage=usage,
            usage_capture_status=usage_capture_status,  # type: ignore[arg-type]
        ),
    )
