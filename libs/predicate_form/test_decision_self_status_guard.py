"""Decision self-status polarity guard — agent-bus thread 1267 regression.

Repro: an upstream predicate-extract LLM step emitted
``status(decision:..., rejected)`` for an *accepted/adopted* decision whose
claim described benching/rejecting a third party (SuperGrok Heavy). The state
token's polarity came from the claim's OBJECT, not the decision's own tracked
state. Class 6 correctly flagged the row; this guard corrects the polarity at
the write-time normalize boundary.

Invariant under test: an accepted decision must NEVER normalize to
``status(self, rejected)`` when its tracked workflow_state is supplied.
"""

from __future__ import annotations

import pytest

from predicate_form import normalize_predicate_domain
from predicate_form.classes import (
    correct_decision_self_status,
    decision_status_token,
    is_decision_self_status,
)
from predicate_form.entity_resolve import StaticEntityResolver
from predicate_form.parser import Predicate

_DECISION = "decision:bench-supergrok-heavy-reviewer-let-subscription-lapse"


def _norm(predicate_form: str, *, workflow_state: str | None):
    """normalize_predicate_domain with an empty resolver (no Class-2 rewrites);
    the decision subject is already prefixed so resolution is irrelevant here."""
    return normalize_predicate_domain(
        _DECISION,
        predicate_form,
        claim_text="Operator decided to bench SuperGrok Heavy and let it lapse.",
        resolver=StaticEntityResolver({}),
        entity_workflow_state=workflow_state,
    )


# --- unit: the guard primitive -------------------------------------------


def test_is_decision_self_status_true_for_self_referential_status() -> None:
    p = Predicate("status", (_DECISION, "rejected"))
    assert is_decision_self_status(_DECISION, p) is True


def test_is_decision_self_status_false_for_other_subject() -> None:
    p = Predicate("status", ("decision:some-other-thing", "rejected"))
    assert is_decision_self_status(_DECISION, p) is False


def test_is_decision_self_status_false_for_non_decision_bearer() -> None:
    p = Predicate("status", ("person:foo", "rejected"))
    assert is_decision_self_status("person:foo", p) is False


def test_decision_status_token_projects_only_generic_states() -> None:
    assert decision_status_token("accepted") == "accepted"
    assert decision_status_token("ACCEPTED") == "accepted"
    assert decision_status_token("superseded") is None  # not a generic-state token
    assert decision_status_token(None) is None
    assert decision_status_token("") is None


def test_correct_rewrites_contradicting_token() -> None:
    p = Predicate("status", (_DECISION, "rejected"))
    out, fired = correct_decision_self_status(_DECISION, p, "accepted")
    assert fired is True
    assert out.args == (_DECISION, "accepted")


def test_correct_is_noop_without_workflow_state() -> None:
    p = Predicate("status", (_DECISION, "rejected"))
    out, fired = correct_decision_self_status(_DECISION, p, None)
    assert fired is False
    assert out.args == (_DECISION, "rejected")


def test_correct_is_noop_when_already_faithful() -> None:
    p = Predicate("status", (_DECISION, "accepted"))
    out, fired = correct_decision_self_status(_DECISION, p, "accepted")
    assert fired is False
    assert out.args == (_DECISION, "accepted")


# --- integration: full normalize_predicate_domain ------------------------


def test_repro_accepted_decision_never_self_rejected() -> None:
    """The exact thread-1267 repro: accepted decision + status(self, rejected)."""
    out = _norm(f"status({_DECISION}, rejected)", workflow_state="accepted")
    assert out["canonical_form"] == f"status({_DECISION}, accepted)"
    assert out["decision_self_status_corrected"] is True
    # Faithful self-status (now matches workflow_state) → review flag cleared.
    assert out["requires_human_review"] is False


def test_corrected_form_is_idempotent_under_renormalize() -> None:
    """normalize(normalize(x)) == normalize(x) with workflow_state context."""
    once = _norm(f"status({_DECISION}, rejected)", workflow_state="accepted")
    twice = _norm(once["canonical_form"], workflow_state="accepted")
    assert twice["canonical_form"] == once["canonical_form"]
    assert twice["decision_self_status_corrected"] is False  # already faithful


def test_no_workflow_state_preserves_prior_class6_flag_behavior() -> None:
    """Without workflow_state context the guard is a no-op and Class 6 still
    flags a generic-state self-status on a decision (prior behavior)."""
    out = _norm(f"status({_DECISION}, rejected)", workflow_state=None)
    assert out["canonical_form"] == f"status({_DECISION}, rejected)"
    assert out["decision_self_status_corrected"] is False
    assert out["requires_human_review"] is True


@pytest.mark.parametrize("bad_token", ["rejected", "cancelled", "deferred"])
def test_various_contradicting_tokens_corrected_to_accepted(bad_token: str) -> None:
    out = _norm(f"status({_DECISION}, {bad_token})", workflow_state="accepted")
    assert out["canonical_form"] == f"status({_DECISION}, accepted)"
    assert out["requires_human_review"] is False
