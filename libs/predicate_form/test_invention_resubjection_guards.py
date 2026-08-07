"""Invention (Check 1) and re-subjection (Check 2) write-time guards — unit layer.

Spec: cortex://notes/system/specs/predicate-form-invention-resubjection-guards.md
PATCH integration lives in ``cortex_store/test_assertion_predicate_form_normalize.py``.
"""

from __future__ import annotations

from predicate_form import normalize_predicate_domain
from predicate_form.entity_resolve import StaticEntityResolver
from predicate_form.invention_resubjection_guards import (
    check_invention,
    check_resubjection,
)
from predicate_form.parser import parse

_ENTITY = "todo:predicate-form-guards-fixture"
_OTHER = "todo:some-other-entity"


def _norm(
    predicate_form: str,
    *,
    entity_id: str = _ENTITY,
    claim_text: str,
) -> dict:
    return normalize_predicate_domain(
        entity_id,
        predicate_form,
        claim_text=claim_text,
        resolver=StaticEntityResolver({}),
    )


# --- unit: guard primitives ------------------------------------------------


def test_check_invention_detects_absent_token() -> None:
    p = parse(f"status({_ENTITY}, operational, current)")
    assert check_invention("Work item is seeded and open.", p) is True


def test_check_invention_claim_present_is_ok() -> None:
    p = parse(f"status({_ENTITY}, seeded, current)")
    assert check_invention("Work item is seeded and open.", p) is False


def test_check_invention_preferred_vocab_ok() -> None:
    p = parse(f"has_attribute({_ENTITY}, current)")
    assert check_invention("No matching words in claim.", p) is False


def test_check_invention_no_claim_is_noop() -> None:
    p = parse(f"status({_ENTITY}, operational, current)")
    assert check_invention(None, p) is False


def test_check_resubjection_mismatch() -> None:
    p = parse(f"has_attribute({_OTHER}, value)")
    assert check_resubjection(_ENTITY, p) is True


def test_check_resubjection_self_subject() -> None:
    p = parse(f"has_attribute({_ENTITY}, value)")
    assert check_resubjection(_ENTITY, p) is False


# --- AC1: invention flag on degenerate operational -------------------------


def test_ac1_invention_operational_flags_normalize() -> None:
    """AC1 — operational absent from claim + vocab → requires_human_review."""
    out = _norm(
        f"status({_ENTITY}, operational, current)",
        claim_text="Work item is open and awaiting implement.",
    )
    assert out["requires_human_review"] is True
    assert out["invention_flag"] is True
    assert out["canonical_form"] == f"status({_ENTITY}, operational, current)"


# --- AC2: re-subjection flag -----------------------------------------------


def test_ac2_resubjection_flags_normalize() -> None:
    out = _norm(
        f"has_attribute({_OTHER}, some_value)",
        claim_text="Bearer owns some_value on itself.",
    )
    assert out["requires_human_review"] is True
    assert out["resubjection_flag"] is True


# --- AC3: three-arg status, non_executable, class_6 arity miss -------------


_DECISION = "decision:non-executable-polarity-fixture"


def test_ac3_three_arg_status_non_executable_flags_without_class6_arity() -> None:
    """AC3 — len(args)!=2 so is_decision_self_status is false; Check 1 still flags."""
    claim = "Decision adopted; bench third-party reviewer."
    out = _norm(
        f"status({_DECISION}, non_executable, current)",
        entity_id=_DECISION,
        claim_text=claim,
    )
    assert out["requires_human_review"] is True
    assert out["invention_flag"] is True
    assert "non_executable" in out["canonical_form"]
