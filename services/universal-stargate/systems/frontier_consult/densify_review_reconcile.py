"""Parse validated densify_review_reconcile closeout payloads."""

from __future__ import annotations

from typing import Any

_RECONCILE_KIND = "densify_review_reconcile"


def parse_densify_review_reconcile(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if payload is None or payload.get("kind") != _RECONCILE_KIND:
        return None
    required = (
        "parent_request_id",
        "review_execution_id",
        "finding_delta",
        "reviewer_concur_only",
        "folded_finding_ids",
    )
    for key in required:
        if key not in payload:
            return None
    finding_delta = payload["finding_delta"]
    if not isinstance(finding_delta, int) or finding_delta < 0:
        return None
    folded = payload["folded_finding_ids"]
    if not isinstance(folded, list) or not all(isinstance(x, str) for x in folded):
        return None
    if not isinstance(payload["reviewer_concur_only"], bool):
        return None
    if not str(payload["parent_request_id"]).strip():
        return None
    if not str(payload["review_execution_id"]).strip():
        return None
    return {
        "parent_request_id": str(payload["parent_request_id"]),
        "review_execution_id": str(payload["review_execution_id"]),
        "finding_delta": finding_delta,
        "reviewer_concur_only": payload["reviewer_concur_only"],
        "folded_finding_ids": folded,
    }
