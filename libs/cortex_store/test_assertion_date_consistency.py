"""Tests for claim-date vs observed_at consistency (audit 2026-08-05 §C1)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, status

from cortex_store.assertion_quality import validate_assertion
from cortex_store.models import AssertionCreate
from cortex_store.routes.assertions import _create_assertion_impl


def test_validate_rejects_claim_date_far_from_observed_at() -> None:
    body = AssertionCreate(
        entity_id="account:chase-mortgage-8787",
        claim="Chase call occurred 2026-08-03 evening with the rep.",
        confidence="confirmed",
        evidence="unit test",
        derivation_type="user_statement",
        valid_from="2026-08-03",
        observed_at="2026-08-05T16:45:09Z",
    )
    result = validate_assertion(body)
    assert result.rejected
    assert any(d.field == "observed_at" for d in result.hard_reject)


def test_validate_allows_claim_date_within_one_day_of_observed_at() -> None:
    body = AssertionCreate(
        entity_id="account:chase-mortgage-8787",
        claim="Chase call occurred 2026-08-04 with Ms. Laura Lambert.",
        confidence="confirmed",
        evidence="unit test",
        derivation_type="user_statement",
        valid_from="2026-08-04",
        observed_at="2026-08-05T16:45:09Z",
    )
    result = validate_assertion(body)
    assert not any(d.field == "observed_at" for d in result.hard_reject)


def test_create_assertion_impl_maps_date_mismatch_to_422() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _create_assertion_impl(
            {
                "entity_id": "account:chase-mortgage-8787",
                "claim": "Call on 2026-08-01 with Chase.",
                "confidence": "confirmed",
                "evidence": "unit test",
                "derivation_type": "user_statement",
                "valid_from": "2026-08-01",
                "observed_at": "2026-08-05T16:45:09Z",
            }
        )
    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
