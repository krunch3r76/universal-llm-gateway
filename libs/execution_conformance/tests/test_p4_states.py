"""State transition guard."""

from __future__ import annotations

import pytest
from execution_conformance.states import (
    ExecutionState,
    IllegalStateTransition,
    transition,
)


def test_p4_states_illegal_transition_raises() -> None:
    with pytest.raises(IllegalStateTransition):
        transition(ExecutionState.completed, ExecutionState.running)


def test_p4_states_reserved_to_accepted() -> None:
    assert transition(ExecutionState.reserved, ExecutionState.accepted) == (
        ExecutionState.accepted
    )
