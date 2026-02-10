"""
State machine for ProcessSupervisor lifecycle management.

Provides explicit state tracking and transitions for the supervisor lifecycle,
replacing implicit state management with Optional fields.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from typing import Any


class SupervisorState(Enum):
    """States for ProcessSupervisor lifecycle."""

    IDLE = "idle"  # Initial state, no worker
    STARTING = "starting"  # Spawning worker process
    CONNECTING = "connecting"  # Connecting to worker transport
    RUNNING = "running"  # Worker running and connected
    STOPPING = "stopping"  # Stopping worker process
    STOPPED = "stopped"  # Worker stopped, ready for cleanup
    ERROR = "error"  # Error state, needs recovery
    SHUTDOWN = "shutdown"  # Supervisor shutting down


class SupervisorEvent(Enum):
    """Events that trigger state transitions."""

    SPAWN_REQUESTED = auto()  # Request to spawn worker
    WORKER_SPAWNED = auto()  # Worker process spawned successfully
    TRANSPORT_CONNECTED = auto()  # Transport connection established
    WORKER_READY = auto()  # Worker reported ready
    STOP_REQUESTED = auto()  # Request to stop worker
    WORKER_STOPPED = auto()  # Worker process stopped
    TRANSPORT_DISCONNECTED = auto()  # Transport connection lost
    ERROR_OCCURRED = auto()  # Error occurred
    SHUTDOWN_REQUESTED = auto()  # Request to shutdown supervisor
    CLEANUP_COMPLETE = auto()  # Cleanup operations completed


@dataclass
class StateTransition:
    """Represents a state transition with validation and side effects."""

    from_state: SupervisorState
    to_state: SupervisorState
    event: SupervisorEvent
    validator: Callable[[dict[str, Any]], bool] | None = None
    side_effect: Callable[[dict[str, Any]], None] | None = None
    description: str = ""


class SupervisorStateMachine:
    """
    State machine for ProcessSupervisor lifecycle management.

    Provides explicit state tracking, validation, and side effects
    for supervisor state transitions.
    """

    def __init__(self):
        """Initialize the state machine with transitions."""
        self._current_state = SupervisorState.IDLE
        self._state_history: list[tuple[SupervisorState, datetime, str]] = []
        self._context: dict[str, Any] = {}

        # Define valid state transitions
        self._transitions = self._build_transition_table()

        # Track state entry/exit times
        self._state_entry_time: datetime | None = None
        self._state_entry_context: dict[str, Any] = {}

    def _build_transition_table(
        self,
    ) -> dict[tuple[SupervisorState, SupervisorEvent], StateTransition]:
        """Build the state transition table."""
        transitions = {}

        # IDLE state transitions
        transitions[(SupervisorState.IDLE, SupervisorEvent.SPAWN_REQUESTED)] = (
            StateTransition(
                SupervisorState.IDLE,
                SupervisorState.STARTING,
                SupervisorEvent.SPAWN_REQUESTED,
                description="Starting worker spawn process",
            )
        )

        # STARTING state transitions
        transitions[(SupervisorState.STARTING, SupervisorEvent.WORKER_SPAWNED)] = (
            StateTransition(
                SupervisorState.STARTING,
                SupervisorState.CONNECTING,
                SupervisorEvent.WORKER_SPAWNED,
                description="Worker spawned, connecting transport",
            )
        )
        transitions[(SupervisorState.STARTING, SupervisorEvent.ERROR_OCCURRED)] = (
            StateTransition(
                SupervisorState.STARTING,
                SupervisorState.ERROR,
                SupervisorEvent.ERROR_OCCURRED,
                description="Error during worker spawn",
            )
        )

        # CONNECTING state transitions
        transitions[
            (SupervisorState.CONNECTING, SupervisorEvent.TRANSPORT_CONNECTED)
        ] = StateTransition(
            SupervisorState.CONNECTING,
            SupervisorState.RUNNING,
            SupervisorEvent.TRANSPORT_CONNECTED,
            description="Transport connected, worker ready",
        )
        transitions[(SupervisorState.CONNECTING, SupervisorEvent.ERROR_OCCURRED)] = (
            StateTransition(
                SupervisorState.CONNECTING,
                SupervisorState.ERROR,
                SupervisorEvent.ERROR_OCCURRED,
                description="Error during transport connection",
            )
        )

        # RUNNING state transitions
        transitions[(SupervisorState.RUNNING, SupervisorEvent.STOP_REQUESTED)] = (
            StateTransition(
                SupervisorState.RUNNING,
                SupervisorState.STOPPING,
                SupervisorEvent.STOP_REQUESTED,
                description="Stopping worker process",
            )
        )
        transitions[
            (SupervisorState.RUNNING, SupervisorEvent.TRANSPORT_DISCONNECTED)
        ] = StateTransition(
            SupervisorState.RUNNING,
            SupervisorState.ERROR,
            SupervisorEvent.TRANSPORT_DISCONNECTED,
            description="Transport disconnected unexpectedly",
        )
        transitions[(SupervisorState.RUNNING, SupervisorEvent.ERROR_OCCURRED)] = (
            StateTransition(
                SupervisorState.RUNNING,
                SupervisorState.ERROR,
                SupervisorEvent.ERROR_OCCURRED,
                description="Error in running state",
            )
        )

        # STOPPING state transitions
        transitions[(SupervisorState.STOPPING, SupervisorEvent.WORKER_STOPPED)] = (
            StateTransition(
                SupervisorState.STOPPING,
                SupervisorState.STOPPED,
                SupervisorEvent.WORKER_STOPPED,
                description="Worker stopped, ready for cleanup",
            )
        )
        transitions[(SupervisorState.STOPPING, SupervisorEvent.ERROR_OCCURRED)] = (
            StateTransition(
                SupervisorState.STOPPING,
                SupervisorState.ERROR,
                SupervisorEvent.ERROR_OCCURRED,
                description="Error during worker stop",
            )
        )

        # STOPPED state transitions
        transitions[(SupervisorState.STOPPED, SupervisorEvent.CLEANUP_COMPLETE)] = (
            StateTransition(
                SupervisorState.STOPPED,
                SupervisorState.IDLE,
                SupervisorEvent.CLEANUP_COMPLETE,
                description="Cleanup complete, returning to idle",
            )
        )
        transitions[(SupervisorState.STOPPED, SupervisorEvent.SPAWN_REQUESTED)] = (
            StateTransition(
                SupervisorState.STOPPED,
                SupervisorState.STARTING,
                SupervisorEvent.SPAWN_REQUESTED,
                description="Starting new worker spawn",
            )
        )

        # ERROR state transitions
        transitions[(SupervisorState.ERROR, SupervisorEvent.CLEANUP_COMPLETE)] = (
            StateTransition(
                SupervisorState.ERROR,
                SupervisorState.IDLE,
                SupervisorEvent.CLEANUP_COMPLETE,
                description="Error cleanup complete, returning to idle",
            )
        )
        transitions[(SupervisorState.ERROR, SupervisorEvent.SPAWN_REQUESTED)] = (
            StateTransition(
                SupervisorState.ERROR,
                SupervisorState.STARTING,
                SupervisorEvent.SPAWN_REQUESTED,
                description="Attempting recovery spawn",
            )
        )

        # SHUTDOWN transitions (from any state)
        for state in SupervisorState:
            if state != SupervisorState.SHUTDOWN:
                transitions[(state, SupervisorEvent.SHUTDOWN_REQUESTED)] = (
                    StateTransition(
                        state,
                        SupervisorState.SHUTDOWN,
                        SupervisorEvent.SHUTDOWN_REQUESTED,
                        description=f"Shutting down from {state.value}",
                    )
                )

        return transitions

    def get_current_state(self) -> SupervisorState:
        """Get the current state."""
        return self._current_state

    def get_state_history(self) -> list[tuple[SupervisorState, datetime, str]]:
        """Get the state transition history."""
        return self._state_history.copy()

    def get_time_in_current_state(self) -> float | None:
        """Get time spent in current state (seconds)."""
        if self._state_entry_time:
            return (datetime.now() - self._state_entry_time).total_seconds()
        return None

    def transition(
        self, event: SupervisorEvent, context: dict[str, Any] | None = None
    ) -> bool:
        """
        Attempt to transition to a new state based on an event.

        Args:
            event: The event triggering the transition
            context: Optional context data for validation/side effects

        Returns:
            bool: True if transition successful, False otherwise
        """
        if context is None:
            context = {}

        # Check if transition is valid
        transition_key = (self._current_state, event)
        if transition_key not in self._transitions:
            return False

        transition = self._transitions[transition_key]

        # Validate transition if validator exists
        if transition.validator and not transition.validator(context):
            return False

        # Record state exit
        exit_time = datetime.now()
        if self._state_entry_time:
            duration = (exit_time - self._state_entry_time).total_seconds()
            self._state_history.append(
                (self._current_state, exit_time, f"Exited after {duration:.2f}s")
            )

        # Perform side effect if exists
        if transition.side_effect:
            transition.side_effect(context)

        # Update state
        old_state = self._current_state
        self._current_state = transition.to_state
        self._state_entry_time = datetime.now()
        self._state_entry_context = context.copy()

        # Record state entry
        self._state_history.append(
            (
                self._current_state,
                self._state_entry_time,
                f"Entered from {old_state.value}: {transition.description}",
            )
        )

        return True

    def can_transition(self, event: SupervisorEvent) -> bool:
        """Check if a transition is possible for the given event."""
        transition_key = (self._current_state, event)
        return transition_key in self._transitions

    def get_valid_events(self) -> set[SupervisorEvent]:
        """Get all valid events for the current state."""
        valid_events = set()
        for state, event in self._transitions.keys():
            if state == self._current_state:
                valid_events.add(event)
        return valid_events

    def get_state_info(self) -> dict[str, Any]:
        """Get comprehensive state information."""
        return {
            "current_state": self._current_state.value,
            "time_in_state": self.get_time_in_current_state(),
            "valid_events": [event.name for event in self.get_valid_events()],
            "state_history": [
                {
                    "state": state.value,
                    "timestamp": timestamp.isoformat(),
                    "description": description,
                }
                for state, timestamp, description in self._state_history[
                    -10:
                ]  # Last 10 transitions
            ],
            "context": self._state_entry_context.copy(),
        }

    def force_state(
        self, new_state: SupervisorState, reason: str = "Forced transition"
    ) -> None:
        """
        Force a state transition (for error recovery).

        Args:
            new_state: The new state to transition to
            reason: Reason for the forced transition
        """
        # Record forced transition
        if self._state_entry_time:
            duration = (datetime.now() - self._state_entry_time).total_seconds()
            self._state_history.append(
                (
                    self._current_state,
                    datetime.now(),
                    f"Forced exit after {duration:.2f}s: {reason}",
                )
            )

        old_state = self._current_state
        self._current_state = new_state
        self._state_entry_time = datetime.now()
        self._state_entry_context = {"reason": reason, "forced": True}

        self._state_history.append(
            (
                self._current_state,
                self._state_entry_time,
                f"Forced transition from {old_state.value}: {reason}",
            )
        )

    def reset(self) -> None:
        """Reset the state machine to IDLE state."""
        self.force_state(SupervisorState.IDLE, "State machine reset")
