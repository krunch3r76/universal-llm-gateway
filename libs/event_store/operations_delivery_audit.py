"""Read-through Event Service operations for the B3 delivery-audit registry."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from .delivery_audit_baseline import summarize_baseline_campaign
from .delivery_audit_registry import (
    derive_token_rollups,
    fetch_parent_by_audit_id,
    fetch_parent_by_correlation,
    list_artifacts_for_audit,
)
from .delivery_audit_selfassess import score_selfassess_rubric
from .store import EventStore

logger = get_logger(__name__)

_PARENT_LOOKUP_KEYS = ("audit_id", "execution_id", "request_id", "dispatch_id")


async def _delivery_audit_parent(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    del store
    provided = {
        key: value
        for key in _PARENT_LOOKUP_KEYS
        if (value := params.get(key)) not in (None, "")
    }
    if len(provided) != 1:
        return {
            "error": (
                "exactly one lookup key is required among "
                "audit_id, execution_id, request_id, dispatch_id"
            )
        }

    lookup_key, lookup_value = next(iter(provided.items()))
    try:
        if lookup_key == "audit_id":
            row = fetch_parent_by_audit_id(str(lookup_value))
        else:
            row, lookup_key = fetch_parent_by_correlation(
                execution_id=params.get("execution_id"),
                request_id=params.get("request_id"),
                dispatch_id=params.get("dispatch_id"),
            )
    except ValueError as exc:
        return {"error": str(exc)}

    return {
        "lookup_key": lookup_key,
        "lookup_value": lookup_value,
        "parent": row,
    }


async def _delivery_audit_artifacts(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    del store
    audit_id = params.get("audit_id")
    if not audit_id:
        return {"error": "audit_id is required"}

    rows = list_artifacts_for_audit(str(audit_id))
    return {
        "audit_id": audit_id,
        "artifacts": rows,
        "count": len(rows),
    }


async def _delivery_audit_token_rollup(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    del store
    audit_id = params.get("audit_id")
    if not audit_id:
        return {"error": "audit_id is required"}
    rows = list_artifacts_for_audit(str(audit_id))
    return {
        "audit_id": audit_id,
        "rollup": derive_token_rollups(rows),
        "count": len(rows),
    }


async def _delivery_audit_baseline_campaign(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    del store
    campaign_id = params.get("campaign_id")
    if not campaign_id:
        return {"error": "campaign_id is required"}
    try:
        return summarize_baseline_campaign(
            str(campaign_id),
            phase=str(params.get("phase") or "baseline"),
            seat_substrate=params.get("seat_substrate"),
            workflow_class=params.get("workflow_class"),
        )
    except ValueError as exc:
        return {"error": str(exc)}


async def _delivery_audit_selfassess(
    params: dict[str, Any],
    store: EventStore,
) -> dict[str, Any]:
    del store
    campaign_id = params.get("campaign_id")
    if not campaign_id:
        return {"error": "campaign_id is required"}
    try:
        return score_selfassess_rubric(
            str(campaign_id),
            phase=str(params.get("phase") or "baseline"),
            seat_substrate=params.get("seat_substrate"),
            workflow_class=params.get("workflow_class"),
        )
    except ValueError as exc:
        return {"error": str(exc)}
