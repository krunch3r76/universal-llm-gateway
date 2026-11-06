"""Persisted execution intent states and monotone transition guard."""

from __future__ import annotations

from enum import StrEnum


class ExecutionState(StrEnum):
    reserved = "reserved"
    accepted = "accepted"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    outcome_unknown = "outcome_unknown"
    blocked_unknown = "blocked_unknown"


TERMINAL_STATES: frozenset[ExecutionState] = frozenset(
    {
        ExecutionState.completed,
        ExecutionState.failed,
        ExecutionState.cancelled,
        ExecutionState.blocked_unknown,
    }
)

# Monotone progression: reserved → accepted → running → terminal/unknown lanes.
LEGAL_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.reserved: frozenset(
        {
            ExecutionState.accepted,
            ExecutionState.running,
            ExecutionState.failed,
            ExecutionState.cancelled,
            ExecutionState.outcome_unknown,
        }
    ),
    ExecutionState.accepted: frozenset(
        {
            ExecutionState.running,
            ExecutionState.completed,
            ExecutionState.failed,
            ExecutionState.cancelled,
            ExecutionState.outcome_unknown,
        }
    ),
    ExecutionState.running: frozenset(
        {
            ExecutionState.completed,
            ExecutionState.failed,
            ExecutionState.cancelled,
            ExecutionState.outcome_unknown,
        }
    ),
    ExecutionState.outcome_unknown: frozenset(
        {
            ExecutionState.blocked_unknown,
            ExecutionState.accepted,
            ExecutionState.running,
            ExecutionState.completed,
            ExecutionState.failed,
        }
    ),
    ExecutionState.blocked_unknown: frozenset({ExecutionState.outcome_unknown}),
    ExecutionState.completed: frozenset(),
    ExecutionState.failed: frozenset(),
    ExecutionState.cancelled: frozenset(),
}


class IllegalStateTransitionError(ValueError):
    """Raised when a transition would violate the monotone table."""


def assert_transition(current: ExecutionState, target: ExecutionState) -> None:
    allowed = LEGAL_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise IllegalStateTransitionError(
            f"illegal transition {current.value!r} → {target.value!r}"
        )


IllegalStateTransition = IllegalStateTransitionError


def transition(current: ExecutionState, target: ExecutionState) -> ExecutionState:
    assert_transition(current, target)
    return target
