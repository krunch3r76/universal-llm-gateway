"""10-case acceptance test set for condition_admission.py.

Per spec tasks/specs/condition-type-and-session-mode.md § Acceptance:
Each of the 10 canonical cases must classify to the documented disposition.

Cases:
  1.  Grief                         → enduring_fact       → admit
  2.  Chronic illness                → enduring_fact       → admit_with_children (maintenance)
  3.  Immigration/employment barrier → blocked             → admit_with_children
  4.  Tax dispute                    → currently_actionable→ route_to_todo
  5.  Recurring maintenance          → recurrent_maintenance→ admit_with_children
  6.  Avoided admin task             → currently_actionable→ route_to_todo
  7.  Blocked legal matter           → blocked             → admit_with_children
  8.  Obsolete trauma reference      → obsolete_reference  → reject
  9.  False condition                → false_condition     → reject
  10. Merged duplicate               → duplicate           → entity_merge
"""

from __future__ import annotations

import pytest

from cortex_store.condition_admission import AdmissionInput, AdmissionResult, classify


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _admit(result: AdmissionResult) -> None:
    assert result.disposition == "admit", f"Expected admit, got {result.disposition!r}: {result.reason}"


def _admit_with_children(result: AdmissionResult) -> None:
    assert result.disposition == "admit_with_children", (
        f"Expected admit_with_children, got {result.disposition!r}: {result.reason}"
    )
    assert result.child_intent is not None, "admit_with_children must carry a child_intent"


def _route_to_todo(result: AdmissionResult) -> None:
    assert result.disposition == "route_to_todo", (
        f"Expected route_to_todo, got {result.disposition!r}: {result.reason}"
    )


def _reject(result: AdmissionResult, expected_category: str) -> None:
    assert result.disposition == "reject", (
        f"Expected reject, got {result.disposition!r}: {result.reason}"
    )
    assert result.category == expected_category, (
        f"Expected category {expected_category!r}, got {result.category!r}"
    )


def _entity_merge(result: AdmissionResult) -> None:
    assert result.disposition == "entity_merge", (
        f"Expected entity_merge, got {result.disposition!r}: {result.reason}"
    )
    assert result.category == "duplicate"


# ──────────────────────────────────────────────────────────────────────────────
# 10 canonical cases
# ──────────────────────────────────────────────────────────────────────────────

def test_case_01_grief_enduring_fact_admit() -> None:
    """Case 1: Grief — enduring_fact → admit."""
    result = classify(
        AdmissionInput(
            slug="condition:grief-parent-loss",
            intent_category="enduring_fact",
            temporality="ongoing",
            has_recurrent_maintenance=False,
        )
    )
    _admit(result)
    assert result.category == "enduring_fact"


def test_case_02_chronic_illness_with_maintenance() -> None:
    """Case 2: Chronic illness — enduring_fact with maintenance → admit_with_children."""
    result = classify(
        AdmissionInput(
            slug="condition:type2-diabetes",
            intent_category="enduring_fact",
            temporality="ongoing",
            has_recurrent_maintenance=True,
        )
    )
    _admit_with_children(result)
    assert result.category == "enduring_fact"
    assert result.child_intent == "recurring_maintenance_task"


def test_case_03_immigration_employment_barrier() -> None:
    """Case 3: Immigration/employment barrier — blocked → admit_with_children."""
    result = classify(
        AdmissionInput(
            slug="condition:work-permit-barrier",
            intent_category="blocked",
            temporality="ongoing",
        )
    )
    _admit_with_children(result)
    assert result.category == "blocked"
    assert result.child_intent == "unblock"


def test_case_04_tax_dispute_route_to_todo() -> None:
    """Case 4: Tax dispute — currently_actionable → route_to_todo (not a condition)."""
    result = classify(
        AdmissionInput(
            slug="condition:irs-dispute-2024",
            intent_category="currently_actionable",
            temporality="ongoing",
        )
    )
    _route_to_todo(result)
    assert result.category == "currently_actionable"


def test_case_05_recurring_maintenance() -> None:
    """Case 5: Recurring maintenance — recurrent_maintenance → admit_with_children."""
    result = classify(
        AdmissionInput(
            slug="condition:home-hvac-filter-maintenance",
            intent_category="recurrent_maintenance",
            temporality="ongoing",
        )
    )
    _admit_with_children(result)
    assert result.category == "recurrent_maintenance"
    assert result.child_intent == "recurring_maintenance_task"


def test_case_06_avoided_admin_task_route_to_todo() -> None:
    """Case 6: Avoided admin task — currently_actionable → route_to_todo (reject as condition)."""
    result = classify(
        AdmissionInput(
            slug="condition:overdue-dentist-appointment",
            intent_category="currently_actionable",
            temporality="episodic",
        )
    )
    _route_to_todo(result)
    assert result.category == "currently_actionable"


def test_case_07_blocked_legal_matter() -> None:
    """Case 7: Blocked legal matter — blocked → admit_with_children."""
    result = classify(
        AdmissionInput(
            slug="condition:estate-probate-blocked",
            intent_category="blocked",
            temporality="ongoing",
        )
    )
    _admit_with_children(result)
    assert result.category == "blocked"


def test_case_08_obsolete_trauma_reference_reject() -> None:
    """Case 8: Obsolete trauma reference — reject (historical + obsolete)."""
    result = classify(
        AdmissionInput(
            slug="condition:childhood-trauma-ref-obsolete",
            intent_category="reflection_only",
            temporality="historical",
            is_obsolete_ref=True,
        )
    )
    _reject(result, "obsolete_reference")


def test_case_09_false_condition_reject() -> None:
    """Case 9: False condition — reject (is_false_admission=True)."""
    result = classify(
        AdmissionInput(
            slug="condition:nonexistent-allergy",
            intent_category="enduring_fact",
            temporality="ongoing",
            is_false_admission=True,
        )
    )
    _reject(result, "false_condition")


def test_case_10_merged_duplicate_entity_merge() -> None:
    """Case 10: Merged duplicate — entity_merge."""
    result = classify(
        AdmissionInput(
            slug="condition:grief-loss-v2",
            intent_category="enduring_fact",
            temporality="ongoing",
            is_duplicate_of="condition:grief-parent-loss",
        )
    )
    _entity_merge(result)
    assert "condition:grief-parent-loss" in result.reason


# ──────────────────────────────────────────────────────────────────────────────
# Edge cases + guard tests
# ──────────────────────────────────────────────────────────────────────────────

def test_invalid_intent_category_raises() -> None:
    with pytest.raises(ValueError, match="intent_category"):
        AdmissionInput(slug="x", intent_category="wishful_thinking")


def test_invalid_temporality_raises() -> None:
    with pytest.raises(ValueError, match="temporality"):
        AdmissionInput(slug="x", intent_category="enduring_fact", temporality="quantum")


def test_reflection_only_admit() -> None:
    """Pure reflection without obsolete flag → admit."""
    result = classify(
        AdmissionInput(slug="condition:old-memory", intent_category="reflection_only", temporality="ongoing")
    )
    assert result.disposition == "admit"
    assert result.category == "reflection_only"


def test_duplicate_takes_precedence_over_false() -> None:
    """Duplicate gate fires before false_admission gate."""
    result = classify(
        AdmissionInput(
            slug="condition:x",
            intent_category="enduring_fact",
            is_duplicate_of="condition:y",
            is_false_admission=True,
        )
    )
    _entity_merge(result)
