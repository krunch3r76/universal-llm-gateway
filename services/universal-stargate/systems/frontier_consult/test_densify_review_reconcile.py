"""Unit tests for densify review reconcile parser + route."""

from __future__ import annotations

from systems.frontier_consult.densify_review_reconcile import (
    parse_densify_review_reconcile,
)


def test_valid_reconcile_payload() -> None:
    parsed = parse_densify_review_reconcile(
        {
            "kind": "densify_review_reconcile",
            "parent_request_id": "req-parent",
            "review_execution_id": "exec-child",
            "finding_delta": 2,
            "reviewer_concur_only": False,
            "folded_finding_ids": ["F001"],
        }
    )
    assert parsed is not None
    assert parsed["finding_delta"] == 2


def test_missing_correlation_rejected() -> None:
    assert (
        parse_densify_review_reconcile(
            {
                "kind": "densify_review_reconcile",
                "review_execution_id": "exec-child",
                "finding_delta": 0,
                "reviewer_concur_only": True,
                "folded_finding_ids": [],
            }
        )
        is None
    )


def test_negative_finding_delta_rejected() -> None:
    assert (
        parse_densify_review_reconcile(
            {
                "kind": "densify_review_reconcile",
                "parent_request_id": "req-parent",
                "review_execution_id": "exec-child",
                "finding_delta": -1,
                "reviewer_concur_only": False,
                "folded_finding_ids": [],
            }
        )
        is None
    )
