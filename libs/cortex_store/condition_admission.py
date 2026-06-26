"""Condition admission gate — deterministic rubric classifier.

Maps a structured intent payload to exactly one disposition:
  admit               — enduring fact or pure reflection; no spawned children
  admit_with_children — enduring fact with maintenance edge, blocked, or
                        recurrent maintenance; spawns closable child todos
  route_to_todo       — currently actionable; create a todo instead
  entity_merge        — duplicate of an existing condition
  reject              — false condition or obsolete reference

Deterministic on structured inputs; no network, no LLM call.

Input shape (AdmissionInput):
  slug               str         proposed entity slug (informational)
  intent_category    str         caller-supplied coarse category
  temporality        str         "ongoing" | "episodic" | "historical"
  is_false_admission bool        caller asserts: this is factually wrong
  is_duplicate_of    str | None  slug of existing condition if duplicate
  is_obsolete_ref    bool        retained only for historical completeness
  has_recurrent_maintenance  bool  condition generates recurring chores
  notes              str | None  optional free-text (not evaluated)

Output shape (AdmissionResult):
  disposition  str   one of: admit | admit_with_children | route_to_todo |
                              entity_merge | reject
  category     str   enduring_fact | recurrent_maintenance |
                     currently_actionable | blocked | reflection_only |
                     obsolete_reference | false_condition | duplicate
  reason       str   human-readable rejection/routing rationale
  child_intent str | None   intent hint for spawned child todos (when
                            disposition == admit_with_children)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AdmissionDisposition = Literal[
    "admit", "admit_with_children", "route_to_todo", "entity_merge", "reject"
]

AdmissionCategory = Literal[
    "enduring_fact",
    "recurrent_maintenance",
    "currently_actionable",
    "blocked",
    "reflection_only",
    "obsolete_reference",
    "false_condition",
    "duplicate",
]

_VALID_INTENT_CATEGORIES = frozenset(
    {
        "enduring_fact",
        "recurrent_maintenance",
        "currently_actionable",
        "blocked",
        "reflection_only",
    }
)

_VALID_TEMPORALITIES = frozenset({"ongoing", "episodic", "historical"})


@dataclass
class AdmissionInput:
    slug: str
    intent_category: str
    temporality: str = "ongoing"
    is_false_admission: bool = False
    is_duplicate_of: str | None = None
    is_obsolete_ref: bool = False
    has_recurrent_maintenance: bool = False
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.intent_category not in _VALID_INTENT_CATEGORIES:
            raise ValueError(
                f"intent_category {self.intent_category!r} not in "
                f"{sorted(_VALID_INTENT_CATEGORIES)}"
            )
        if self.temporality not in _VALID_TEMPORALITIES:
            raise ValueError(
                f"temporality {self.temporality!r} not in "
                f"{sorted(_VALID_TEMPORALITIES)}"
            )


@dataclass
class AdmissionResult:
    disposition: AdmissionDisposition
    category: AdmissionCategory
    reason: str
    child_intent: str | None = field(default=None)


def classify(inp: AdmissionInput) -> AdmissionResult:
    """Run the admission rubric against *inp*; return an AdmissionResult.

    Decision tree — evaluated top-to-bottom; first matching branch wins.
    """
    # Gate 1: duplicate → entity_merge (never create a second entity)
    if inp.is_duplicate_of:
        return AdmissionResult(
            disposition="entity_merge",
            category="duplicate",
            reason=(
                f"Duplicate of existing condition {inp.is_duplicate_of!r}. "
                "Use entity_merge rather than creating a new entity."
            ),
        )

    # Gate 2: false admission → reject
    if inp.is_false_admission:
        return AdmissionResult(
            disposition="reject",
            category="false_condition",
            reason=(
                "Caller asserts this is not a real condition (is_false_admission=true). "
                "Correct the record at the source rather than admitting."
            ),
        )

    # Gate 3: obsolete reference with no ongoing relevance → reject
    if inp.is_obsolete_ref and inp.temporality == "historical":
        return AdmissionResult(
            disposition="reject",
            category="obsolete_reference",
            reason=(
                "Obsolete historical reference with no ongoing relevance. "
                "If previously admitted, recategorize the existing entity to "
                "workflow_state=historical rather than creating a duplicate."
            ),
        )

    # Gate 4: currently actionable → route to todo (not a condition)
    if inp.intent_category == "currently_actionable":
        return AdmissionResult(
            disposition="route_to_todo",
            category="currently_actionable",
            reason=(
                "This intent describes actionable work, not a standing fact. "
                "Create a closable todo entity instead."
            ),
        )

    # Gate 5: blocked → admit + spawn blocking child todo
    if inp.intent_category == "blocked":
        return AdmissionResult(
            disposition="admit_with_children",
            category="blocked",
            reason="Blocked standing condition; spawn a closable child todo for the unblock step.",
            child_intent="unblock",
        )

    # Gate 6: recurrent maintenance → admit + spawn recurring children
    if inp.intent_category == "recurrent_maintenance":
        return AdmissionResult(
            disposition="admit_with_children",
            category="recurrent_maintenance",
            reason="Recurrent maintenance condition; spawn recurring closable child todos.",
            child_intent="recurring_maintenance_task",
        )

    # Gate 7: enduring fact with recurrent maintenance edge
    if inp.intent_category == "enduring_fact" and inp.has_recurrent_maintenance:
        return AdmissionResult(
            disposition="admit_with_children",
            category="enduring_fact",
            reason="Enduring fact with recurring maintenance actions; spawn maintenance child todos.",
            child_intent="recurring_maintenance_task",
        )

    # Gate 8: pure enduring fact or reflection → admit
    if inp.intent_category in {"enduring_fact", "reflection_only"}:
        return AdmissionResult(
            disposition="admit",
            category=inp.intent_category,  # type: ignore[arg-type]
            reason="Standing condition admitted as a closure-exempt enduring fact.",
        )

    # Fallback (unreachable given __post_init__ guard, but satisfies type checker)
    return AdmissionResult(
        disposition="reject",
        category="false_condition",
        reason=f"Unclassified intent_category {inp.intent_category!r}; rejecting as safety default.",
    )


__all__ = ["AdmissionInput", "AdmissionResult", "classify"]
