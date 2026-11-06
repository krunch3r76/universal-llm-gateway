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


STATE_RANK: dict[ExecutionState, int] = {
    ExecutionState.reserved: 0,
    ExecutionState.accepted: 1,
    ExecutionState.running: 2,
    ExecutionState.completed: 3,
    ExecutionState.failed: 3,
    ExecutionState.cancelled: 3,
    ExecutionState.blocked_unknown: 3,
}


TERMINAL_STATES: frozenset[ExecutionState] = frozenset(
    {
        ExecutionState.completed,
        ExecutionState.failed,
        ExecutionState.cancelled,
    }
)


LEGAL_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.reserved: frozenset(
        {
            ExecutionState.accepted,
            ExecutionState.running,
            ExecutionState.completed,
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
            ExecutionState.cancelled,
        }
    ),
    ExecutionState.blocked_unknown: frozenset(
        {
            ExecutionState.outcome_unknown,
            ExecutionState.completed,
            ExecutionState.failed,
            ExecutionState.cancelled,
        }
    ),
    ExecutionState.completed: frozenset(),
    ExecutionState.failed: frozenset(),
    ExecutionState.cancelled: frozenset(),
}


class IllegalStateTransitionError(ValueError):
    """Raised when a transition would violate the monotone table."""


IllegalStateTransition = IllegalStateTransitionError


def assert_transition(current: ExecutionState, target: ExecutionState) -> None:
    if current == target:
        return
    allowed = LEGAL_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise IllegalStateTransitionError(
            f"illegal transition {current.value!r} → {target.value!r}"
        )


def adoption_target(local: ExecutionState, owner: ExecutionState) -> ExecutionState:
    """Resolve owner adoption; ``outcome_unknown`` is outside rank order."""
    if owner == ExecutionState.outcome_unknown:
        return local
    if local == ExecutionState.outcome_unknown:
        return owner
    if STATE_RANK.get(owner, 0) < STATE_RANK.get(local, 0):
        return local
    return owner
