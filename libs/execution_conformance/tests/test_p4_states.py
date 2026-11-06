"""State transition guard."""

from __future__ import annotations

import pytest
from execution_conformance.states import (
    ExecutionState,
    IllegalStateTransitionError,
    assert_transition,
)


def test_p4_states_illegal_transition_raises() -> None:
    with pytest.raises(IllegalStateTransitionError):
        assert_transition(ExecutionState.completed, ExecutionState.running)


def test_p4_states_reserved_to_completed() -> None:
    assert_transition(ExecutionState.reserved, ExecutionState.completed)


def test_p4_states_idempotent_same_state() -> None:
    assert_transition(ExecutionState.outcome_unknown, ExecutionState.outcome_unknown)
