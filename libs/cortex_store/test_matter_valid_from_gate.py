"""Matter-scope valid_from XOR valid_from_unknown_reason write gate (Fable dd1858ae Q5)."""

from __future__ import annotations

from cortex_store.assertion_quality import validate_assertion
from cortex_store.models import AssertionCreate

_MATTER_ENTITY = "case:chase-escrow-flintridge-2026"
_OUT_OF_SCOPE_ENTITY = "todo:test-unrelated-item"


def _body(entity_id: str = _MATTER_ENTITY, **overrides: object) -> AssertionCreate:
    base: dict[str, object] = {
        "entity_id": entity_id,
        "claim": "Escrow analysis shows a projected shortfall.",
        "confidence": "believed",
        "evidence": "unit test",
        "derivation_type": "inference",
        "reasoning_summary": "fixture",
        "observed_at": "2026-07-30T00:00:00Z",
    }
    base.update(overrides)
    return AssertionCreate(**base)  # type: ignore[arg-type]


def test_matter_scope_rejects_missing_valid_from_and_unknown_reason() -> None:
    result = validate_assertion(_body(valid_from=None))
    assert result.rejected
    assert any(d.field == "valid_from" for d in result.hard_reject)


def test_matter_scope_accepts_with_valid_from() -> None:
    result = validate_assertion(_body(valid_from="2026-06-26"))
    assert not result.rejected


def test_matter_scope_accepts_unknown_reason_only() -> None:
    result = validate_assertion(
        _body(
            valid_from=None,
            attributes={"valid_from_unknown_reason": "servicer letter undated"},
        )
    )
    assert not result.rejected


def test_matter_scope_unknown_reason_allows_dated_claim_without_valid_from() -> None:
    result = validate_assertion(
        _body(
            claim="Chase denied spread extension on 2026-06-26.",
            valid_from=None,
            attributes={
                "valid_from_unknown_reason": "effective date not stated in source"
            },
        )
    )
    assert not result.rejected


def test_out_of_scope_entity_without_valid_from_uses_prior_rules_only() -> None:
    result = validate_assertion(
        _body(
            entity_id=_OUT_OF_SCOPE_ENTITY,
            claim="Simple claim without calendar references.",
            valid_from=None,
        )
    )
    assert not result.rejected
    assert not any(d.field == "valid_from" for d in result.hard_reject)


def test_out_of_scope_dated_claim_still_requires_valid_from() -> None:
    result = validate_assertion(
        _body(
            entity_id=_OUT_OF_SCOPE_ENTITY,
            claim="Payment posted on 2026-03-15.",
            valid_from=None,
        )
    )
    assert result.rejected
    assert any(d.field == "valid_from" for d in result.hard_reject)


def test_matter_scope_rejects_empty_unknown_reason() -> None:
    result = validate_assertion(
        _body(
            valid_from=None,
            attributes={"valid_from_unknown_reason": "   "},
        )
    )
    assert result.rejected
    assert any(d.field == "valid_from_unknown_reason" for d in result.hard_reject)


def test_matter_scope_rejects_both_valid_from_and_unknown_reason() -> None:
    result = validate_assertion(
        _body(
            valid_from="2026-06-26",
            attributes={"valid_from_unknown_reason": "should not pair with valid_from"},
        )
    )
    assert result.rejected
    assert any(d.field == "valid_from" for d in result.hard_reject)


def test_matter_scope_child_entity_prefix_is_gated() -> None:
    result = validate_assertion(
        _body(
            entity_id=f"{_MATTER_ENTITY}/escrow-analysis",
            valid_from=None,
        )
    )
    assert result.rejected
    assert any(d.field == "valid_from" for d in result.hard_reject)


def test_account_hub_scope_is_gated() -> None:
    result = validate_assertion(
        _body(entity_id="account:chase-mortgage-8787", valid_from=None)
    )
    assert result.rejected


def test_boe19p_hub_scope_is_gated() -> None:
    result = validate_assertion(
        _body(
            entity_id="case:boe19p-flintridge-appeal-2026",
            valid_from=None,
        )
    )
    assert result.rejected
