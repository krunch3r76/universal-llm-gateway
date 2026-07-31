"""Phase 4 carry-over debt #1 — predicate-form round-trip for the
cortex-chat-openai compactor.

Phase 3's anchor.py + window.py read ``predicate_form`` matching
``user_turn(N)`` / ``assistant_turn(N)`` literally. Cortex writes go
through ``_normalize_predicate_form_for_write`` ->
``normalize_predicate_domain`` (libs/cortex_store/routes/assertions/
_shared.py:139). This test pins the invariant that the normalizer
preserves the turn-predicate forms exactly so the Phase 3 read path
never silently returns empty windows.

Trace (re-derivable from libs/predicate_form/classes.py):

- Class 1 (state synonyms): registry maps only
  ``reassigned_to_another_department -> reassigned``. No rewrite on
  ``user_turn`` / ``assistant_turn`` / numeric args.
- Class 4 (shape variants): only fires on predicate name
  ``has_attribute`` with 2 args. ``user_turn(N)`` has name
  ``user_turn`` and 1 arg. No rewrite.
- Class 3 (case-fold): numeric args fail both the case-ID heuristic
  and the month-date regex. No rewrite.
- Class 2 (entity-prefix): skips args where ``_is_numeric(arg)`` is
  True. Numeric ``N`` is skipped. No rewrite.
- Class 6 (generic-state guard): checks last arg against
  ``CLASS_6_GENERIC_STATES``. Numeric ``N`` is never in that
  frozenset. No flag.

Therefore: ``canonical_form == predicate_form`` for all integer N.
"""

from __future__ import annotations

import pytest

from predicate_form import StaticEntityResolver, normalize_predicate_domain


def _no_match_resolver() -> StaticEntityResolver:
    """Resolver that returns no matches — matches the chat-anchor reality."""
    return StaticEntityResolver({})


@pytest.mark.parametrize("turn_idx", [0, 1, 7, 42, 999, 1234567])
def test_user_turn_predicate_preserved_round_trip(turn_idx: int) -> None:
    predicate_form = f"user_turn({turn_idx})"
    result = normalize_predicate_domain(
        entity_id="thread:openai-chat:abc-xyz",
        predicate_form=predicate_form,
        resolver=_no_match_resolver(),
    )
    assert result["canonical_form"] == predicate_form, (
        f"normalizer mangled user_turn({turn_idx}): "
        f"in={predicate_form!r} out={result['canonical_form']!r}"
    )
    assert result["domain_key"] == predicate_form
    assert result["classes_applied"] == []
    assert result["requires_human_review"] is False


@pytest.mark.parametrize("turn_idx", [0, 1, 7, 42, 999, 1234567])
def test_assistant_turn_predicate_preserved_round_trip(turn_idx: int) -> None:
    predicate_form = f"assistant_turn({turn_idx})"
    result = normalize_predicate_domain(
        entity_id="thread:openai-chat:abc-xyz",
        predicate_form=predicate_form,
        resolver=_no_match_resolver(),
    )
    assert result["canonical_form"] == predicate_form, (
        f"normalizer mangled assistant_turn({turn_idx}): "
        f"in={predicate_form!r} out={result['canonical_form']!r}"
    )
    assert result["domain_key"] == predicate_form
    assert result["classes_applied"] == []
    assert result["requires_human_review"] is False


def test_thread_entity_id_not_in_workflow_types() -> None:
    """``thread:`` is not in ``CLASS_6_WORKFLOW_ENTITY_TYPES``.

    Confirms Class 6 would flag a generic-state predicate on a
    thread-typed entity. Irrelevant for legitimate turn predicates
    (numeric last-arg never matches ``CLASS_6_GENERIC_STATES``) but
    worth pinning so a future contributor doesn't accidentally use
    ``pending`` / ``active`` as a turn marker without realising it
    would trip the audit gate.
    """
    result = normalize_predicate_domain(
        entity_id="thread:openai-chat:abc-xyz",
        predicate_form="user_turn(active)",  # synthetic — never written
        resolver=_no_match_resolver(),
    )
    assert result["requires_human_review"] is True
    assert 6 in result["classes_applied"]
