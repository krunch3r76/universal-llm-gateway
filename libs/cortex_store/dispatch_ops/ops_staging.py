"""Cortex dispatch ops for extraction_staging — review-seat batch approve."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from ..models import StagingApproval, StagingBatchApproval
from ..routes.staging import (
    approve_staging,
    approve_staging_batch,
    list_staging,
    reject_staging,
)


def _op_staging_list(
    status: str | None = None,
    source_uri: str | None = None,
    limit: int = 50,
    **_: object,
) -> dict[str, Any]:
    result = list_staging(status_filter=status, source_uri=source_uri, limit=limit)
    return result.model_dump(mode="json")


def _op_staging_batch_approve(
    staging_ids: list[int] | None = None,
    ledger_id: int | None = None,
    reviewer: str = "web-anthropic-opus-review",
    **_: object,
) -> dict[str, Any]:
    if not staging_ids:
        return {"error": "staging_ids is required", "code": "missing_staging_ids"}
    body = StagingBatchApproval(
        staging_ids=[int(x) for x in staging_ids],
        reviewer=reviewer,
        ledger_id=int(ledger_id) if ledger_id is not None else None,
    )
    try:
        result = approve_staging_batch(body)
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    return result.model_dump(mode="json")


def _op_staging_approve(
    staging_id: int | None = None,
    reviewer: str = "web-anthropic-opus-review",
    **_: object,
) -> dict[str, Any]:
    if staging_id is None:
        return {"error": "staging_id is required", "code": "missing_staging_id"}
    try:
        result = approve_staging(int(staging_id), StagingApproval(reviewer=reviewer))
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    return result.model_dump(mode="json")


def _op_staging_reject(
    staging_id: int | None = None,
    reviewer: str = "web-anthropic-opus-review",
    **_: object,
) -> dict[str, Any]:
    if staging_id is None:
        return {"error": "staging_id is required", "code": "missing_staging_id"}
    try:
        result = reject_staging(int(staging_id), StagingApproval(reviewer=reviewer))
    except HTTPException as exc:
        return {"error": exc.detail, "status_code": exc.status_code}
    return result.model_dump(mode="json")


__all__ = [
    "_op_staging_approve",
    "_op_staging_batch_approve",
    "_op_staging_list",
    "_op_staging_reject",
]
