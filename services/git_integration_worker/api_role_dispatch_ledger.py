"""Persist API-role team_dispatch economics onto the GIW work-key ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from dispatch_economics.api_closeout import normalize_api_role_usage

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
    if usage is not None:
        usage = normalize_api_role_usage(usage, model=resolved_model)
    ledger = CursorDispatchLedger.instance()
    stamp = datetime.now(UTC).isoformat()
    record_json = json.dumps(
        {
            "usage": usage,
            "usage_capture_status": usage_capture_status,
            "substrate": "api_role",
        }
    )
    dispatch_id = ledger.upsert_api_role_completed_dispatch(
        execution_id=execution_id,
        work_key=work_key,
        resolved_model=resolved_model,
        contract=contract,
        record_json=record_json,
        stamp=stamp,
    )
    persist_dispatch_usage(
        ledger,
        dispatch_id=dispatch_id,
        record=DispatchUsageRecord(
            usage=usage,
            usage_capture_status=usage_capture_status,  # type: ignore[arg-type]
        ),
    )
