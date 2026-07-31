"""Regression tests for polarity status-antonym scoping (agent-bus thread 1197).

The bag-of-words antonym match in ``detect_polarity_conflict`` must fire on
short status assertions ("enabled" vs "disabled") but must NOT fire on long
decision/friction prose that accrues generic status tokens incidentally
(resolved/unresolved, open/closed, ...) without subject alignment.
"""

from __future__ import annotations

from cortex_store.polarity import detect_polarity_conflict

# Excerpts approximating the real claims (assertions 12657 vs 12644) on
# decision:handoff-surface-consistency that produced the false 409.
_LONG_A = (
    "Two 2-A convention decisions, operator-delegated to claude-web with "
    "criterion most optimal for agents (additive follow-on to assertion "
    "12629, NOT a contradiction of it): missing/unbalanced/duplicate markers "
    "-> unresolved (marked, never silent-stale)."
)
_LONG_B = (
    "RESOLVED + LIVE (thread 1188 turns 8-10): the close-path INSERT OR "
    "IGNORE drift hole is fixed and deployed. cursor added an unconditional "
    "UPDATE entities SET attributes after the INSERT OR IGNORE."
)


def test_long_decision_prose_does_not_false_fire():
    """Incidental resolved/unresolved tokens in long prose -> no conflict."""
    assert detect_polarity_conflict(_LONG_A, _LONG_B) is False


def test_short_status_antonyms_still_fire():
    assert (
        detect_polarity_conflict(
            "worker llama3 is enabled", "worker llama3 is disabled"
        )
        is True
    )
    assert detect_polarity_conflict("fork is resolved", "fork is unresolved") is True
    assert (
        detect_polarity_conflict("node edge is active", "node edge is inactive") is True
    )


def test_short_non_conflicting_claims_are_safe():
    assert detect_polarity_conflict("worker is enabled", "node is active") is False


def test_one_long_one_short_skips_rule():
    """A long claim on either side disqualifies the bag-of-words match."""
    assert detect_polarity_conflict(_LONG_A, "fork is resolved") is False
